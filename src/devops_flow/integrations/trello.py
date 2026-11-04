"""Client REST Trello v1 ; aucun import UI, SQL ni configuration implicite."""
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import re
import time
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from devops_flow.integrations.config import TrelloCredentials

MESSAGES = {
    'unauthorized': 'Connexion Trello refusée : vérifiez la clé et le token.',
    'forbidden': 'Permissions Trello insuffisantes : autorisez la lecture et l’écriture des boards.',
    'missing': 'La ressource Trello est supprimée ou inaccessible. Vous pouvez recréer un nouveau board.',
    'rate_limit': 'Limite de requêtes Trello atteinte. Réessayez après le délai indiqué.',
    'unavailable': 'Trello est temporairement indisponible. Vous pouvez reprendre la projection.',
    'response': 'La réponse de Trello est invalide. Vous pouvez réessayer.',
    'request': 'Trello a refusé la demande. Vérifiez les données et les limites de votre compte.',
    'uncertain': 'Le résultat d’une création Trello reste incertain. Vérifiez Trello puis reprenez ; aucune création ne sera répétée sans vérification.',
    'ambiguous': 'Plusieurs ressources Trello correspondent à cette opération. Vérifiez le board avant de reprendre.',
    'closed': 'Le board ou une liste Trello est archivé. Rouvrez-le dans Trello ou recréez un nouveau board.',
}


class TrelloError(ValueError):
    def __init__(self, code: str, *, retry_at: float | None = None):
        self.code, self.retry_at = code, retry_at
        super().__init__(MESSAGES[code])


@dataclass(frozen=True)
class TrelloBoard:
    id: str
    name: str
    url: str


@dataclass(frozen=True)
class TrelloList:
    id: str
    name: str


@dataclass(frozen=True)
class TrelloCard:
    id: str
    name: str
    url: str


@dataclass(frozen=True)
class TrelloProjection:
    board: TrelloBoard
    lists: dict[str, TrelloList]
    cards: tuple[TrelloCard, ...]


class TrelloClient(Protocol):
    def ping(self) -> str: ...
    def create_board(self, name: str, *, desc: str = '') -> TrelloBoard: ...
    def create_list(self, board_id: str, name: str) -> TrelloList: ...
    def create_card(self, list_id: str, name: str, desc: str = '') -> TrelloCard: ...
    def create_checklist(self, card_id: str, name: str, items: tuple[str, ...]) -> str: ...
    def list_boards(self) -> tuple[TrelloBoard, ...]: ...
    def find_boards(self, marker: str) -> tuple[TrelloBoard, ...]: ...
    def get_board(self, board_id: str) -> TrelloBoard: ...
    def get_lists(self, board_id: str) -> tuple[dict, ...]: ...
    def get_cards(self, board_id: str) -> tuple[dict, ...]: ...
    def get_checklists(self, card_id: str) -> tuple[dict, ...]: ...
    def create_empty_checklist(self, card_id: str, name: str) -> str: ...
    def create_checkitem(self, checklist_id: str, name: str) -> str: ...
    def close(self) -> None: ...


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise TrelloError('response')
    return value


def trello_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise TrelloError('response')
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.hostname != 'trello.com' or url.username
                or url.password or url.port not in (None, 443) or url.query or url.fragment
                or not url.path.startswith(('/b/', '/c/'))):
            raise ValueError
    except ValueError:
        raise TrelloError('response') from None
    return value


def board_from(data):
    try:
        if not isinstance(data['name'], str):
            raise ValueError
        return TrelloBoard(identifier(data['id']), data['name'], trello_url(data['url']))
    except (KeyError, TypeError, ValueError):
        raise TrelloError('response') from None


def card_from(data):
    board = board_from(data)
    return TrelloCard(board.id, board.name, board.url)


class HttpTrelloClient:
    BASE_URL = 'https://api.trello.com/1'

    def __init__(self, credentials: TrelloCredentials, timeout: float = 10.0,
                 *, transport: httpx.BaseTransport | None = None, sleep=time.sleep, clock=time.time):
        self._creds = credentials
        self._sleep, self._clock = sleep, clock
        self._client = httpx.Client(timeout=timeout, transport=transport,
                                    trust_env=False, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._client.close()

    def _auth(self) -> dict[str, str]:
        return {'key': self._creds.api_key, 'token': self._creds.token}

    def _delay(self, value):
        try:
            delay = float(value)
            if delay >= 0 and delay < float('inf'):
                return delay
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                return max(0, date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - self._clock())
            except (ValueError, TypeError, OverflowError):
                pass
        return 10.0

    def _request(self, method, path, **fields):
        auth = self._auth()
        headers = {'Authorization': 'OAuth oauth_consumer_key="' + quote(auth['key'], safe='')
                   + '", oauth_token="' + quote(auth['token'], safe='') + '"'}
        for attempt in range(3):
            try:
                response = self._client.request(method, self.BASE_URL + path, headers=headers,
                                                **({'params': fields} if method == 'GET' else {'data': fields}))
            except httpx.TransportError:
                if method != 'GET':
                    raise TrelloError('uncertain') from None
                if attempt == 2:
                    raise TrelloError('unavailable') from None
                self._sleep(2 ** attempt)
                continue
            code = response.status_code
            if code == 429:
                delay = self._delay(response.headers.get('Retry-After'))
                if delay > 60 or attempt == 2:
                    raise TrelloError('rate_limit', retry_at=self._clock() + delay)
                self._sleep(delay)
                continue
            if code >= 500:
                if method != 'GET':
                    raise TrelloError('uncertain')
                if attempt == 2:
                    raise TrelloError('unavailable')
                self._sleep(2 ** attempt)
                continue
            if not 200 <= code < 300:
                raise TrelloError({401: 'unauthorized', 403: 'forbidden', 404: 'missing'}.get(code, 'request'))
            try:
                return response.json()
            except (ValueError, UnicodeError):
                raise TrelloError('response' if method == 'GET' else 'uncertain') from None

    def _collection(self, path, **fields):
        data = self._request('GET', path, **fields)
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise TrelloError('response')
        return tuple(data)

    def ping(self):
        data = self._request('GET', '/members/me', fields='username')
        if not isinstance(data, dict) or not isinstance(data.get('username'), str) or not data['username']:
            raise TrelloError('response')
        return data['username']

    def create_board(self, name, *, desc=''):
        return board_from(self._request('POST', '/boards', name=name, desc=desc,
                                        defaultLists='false', defaultLabels='false', prefs_permissionLevel='private'))

    def create_list(self, board_id, name):
        data = self._request('POST', '/lists', idBoard=identifier(board_id), name=name, pos='bottom')
        try:
            if not isinstance(data['name'], str):
                raise ValueError
            return TrelloList(identifier(data['id']), data['name'])
        except (KeyError, TypeError, ValueError):
            raise TrelloError('response') from None

    def create_card(self, list_id, name, desc=''):
        return card_from(self._request('POST', '/cards', idList=identifier(list_id), name=name, desc=desc, pos='bottom'))

    def create_empty_checklist(self, card_id, name):
        data = self._request('POST', '/checklists', idCard=identifier(card_id), name=name, pos='bottom')
        try:
            return identifier(data['id'])
        except (KeyError, TypeError):
            raise TrelloError('response') from None

    def create_checkitem(self, checklist_id, name):
        data = self._request('POST', f'/checklists/{identifier(checklist_id)}/checkItems',
                             name=name, checked='false', pos='bottom')
        try:
            return identifier(data['id'])
        except (KeyError, TypeError):
            raise TrelloError('response') from None

    def create_checklist(self, card_id, name, items):
        checklist_id = self.create_empty_checklist(card_id, name)
        for item in items:
            self.create_checkitem(checklist_id, item)
        return checklist_id

    def list_boards(self):
        return tuple(board_from(x) for x in self._collection('/members/me/boards', fields='id,name,url', filter='all'))

    def find_boards(self, marker):
        return tuple(board_from(x) for x in self._collection('/members/me/boards', fields='id,name,url,desc', filter='all')
                     if isinstance(x.get('desc'), str) and marker in x['desc'].splitlines())

    def get_board(self, board_id):
        data = self._request('GET', f'/boards/{identifier(board_id)}', fields='id,name,url,closed')
        board = board_from(data)
        if data.get('closed') is not False:
            raise TrelloError('closed' if data.get('closed') is True else 'response')
        return board

    def get_lists(self, board_id):
        return self._collection(f'/boards/{identifier(board_id)}/lists', fields='id,name,closed', filter='all')

    def get_cards(self, board_id):
        return self._collection(f'/boards/{identifier(board_id)}/cards', fields='id,name,url,desc', filter='all')

    def get_checklists(self, card_id):
        return self._collection(f'/cards/{identifier(card_id)}/checklists', fields='id,name',
                                checkItems='all', checkItem_fields='id,name,state')
