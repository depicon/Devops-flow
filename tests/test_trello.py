"""Contrats HTTP et configuration ; aucune requête réseau réelle."""
from datetime import datetime, timezone
from email.utils import format_datetime
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from devops_flow.integrations.config import TrelloCredentials, load_credentials
from devops_flow.integrations.trello import HttpTrelloClient, TrelloError, trello_url


class FakeTrello:
    """Serveur REST en mémoire partagé entre clients et redémarrages simulés."""
    def __init__(self):
        self.boards, self.lists, self.cards, self.checklists = {}, {}, {}, {}
        self.calls = []
        self.fail = None
        self.before = None
        self.closed_clients = 0

    def client(self, **kwargs):
        return HttpTrelloClient(TrelloCredentials('API_KEY_SENTINEL', 'TOKEN_SENTINEL'),
                                transport=httpx.MockTransport(self.request), sleep=lambda _: None, **kwargs)

    def request(self, request):
        self.calls.append(request)
        if self.before:
            response = self.before(request)
            if response is not None:
                return response
        response = self.route(request)
        # Le mode « réponse perdue » applique la création avant l'erreur.
        if self.fail:
            result = self.fail(request, response)
            if result is not None:
                return result
        return response

    def route(self, request):
        path = request.url.path.removeprefix('/1')
        query = dict(request.url.params)
        data = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        def ok(value):
            return httpx.Response(200, json=value)
        if request.method == 'GET':
            if path == '/members/me':
                return ok({'username': 'utilisateur-test'})
            if path == '/members/me/boards':
                return ok(list(self.boards.values()))
            if path.startswith('/boards/'):
                parts = path.split('/')
                board = self.boards.get(parts[2])
                if board is None:
                    return httpx.Response(404)
                if len(parts) == 3:
                    return ok(board)
                if parts[3] == 'lists':
                    return ok([x for x in self.lists.values() if x['idBoard'] == board['id']])
                if parts[3] == 'cards':
                    return ok([x for x in self.cards.values() if x['idBoard'] == board['id']])
            if path.startswith('/cards/') and path.endswith('/checklists'):
                card = path.split('/')[2]
                if card not in self.cards:
                    return httpx.Response(404)
                return ok([x for x in self.checklists.values() if x['idCard'] == card])
        if request.method == 'POST':
            if path == '/boards':
                uid = 'b' + str(len(self.boards) + 1)
                result = {'id': uid, 'name': data['name'], 'desc': data['desc'],
                          'url': 'https://trello.com/b/' + uid, 'closed': False}
                self.boards[uid] = result
                return ok(result)
            if path == '/lists':
                uid = 'l' + str(len(self.lists) + 1)
                result = {'id': uid, **data, 'closed': False}
                self.lists[uid] = result
                return ok(result)
            if path == '/cards':
                uid = 'c' + str(len(self.cards) + 1)
                result = {'id': uid, **data, 'url': 'https://trello.com/c/' + uid,
                          'idBoard': self.lists[data['idList']]['idBoard']}
                self.cards[uid] = result
                return ok(result)
            if path == '/checklists':
                uid = 'k' + str(len(self.checklists) + 1)
                result = {'id': uid, **data, 'checkItems': []}
                self.checklists[uid] = result
                return ok(result)
            if path.startswith('/checklists/') and path.endswith('/checkItems'):
                checklist = self.checklists[path.split('/')[2]]
                result = {'id': 'i' + str(len(checklist['checkItems']) + 1), **data, 'state': 'incomplete'}
                checklist['checkItems'].append(result)
                return ok(result)
        raise AssertionError(f'Unexpected simulated route: {request.method} {path}, {query}')


def test_configuration_explicit_file_precedence_and_no_interpolation(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('TRELLO_API_KEY=file-key\nTRELLO_TOKEN=${DO_NOT_EXPAND}\n', encoding='utf-8')
    monkeypatch.setenv('DO_NOT_EXPAND', 'secret')
    credentials = load_credentials(environ={'TRELLO_API_KEY': 'env-key'}, env_file=path)
    assert credentials.api_key == 'env-key' and credentials.token == '${DO_NOT_EXPAND}'
    assert 'env-key' not in repr(credentials) and '${DO_NOT_EXPAND}' not in repr(credentials)
    assert load_credentials(environ={'TRELLO_TOKEN': ''}, env_file=path) is None
    assert load_credentials(environ={}, env_file=tmp_path / 'absent') is None
    # La présence des deux variables évite même d'ouvrir le fichier.
    assert load_credentials(environ={'TRELLO_API_KEY': 'a', 'TRELLO_TOKEN': 'b'}, env_file=tmp_path).token == 'b'


@pytest.mark.parametrize('key,token', [('', 'b'), ('a', ''), ('a\nsecret', 'b'), ('a', 'b\x00'), ('a', 'b' * 4097)])
def test_credentials_validation(key, token):
    with pytest.raises(ValueError, match='configuration Trello'):
        TrelloCredentials(key, token)


def test_config_rejects_symlink_and_large_file(tmp_path):
    path = tmp_path / 'config'
    path.write_text('x' * 65537)
    link = tmp_path / '.env'
    link.symlink_to(path)
    for candidate in (link, path, tmp_path):
        with pytest.raises(ValueError):
            load_credentials(environ={}, env_file=candidate)


def test_rest_methods_and_authentication(caplog):
    caplog.set_level(logging.DEBUG)
    api = FakeTrello()
    with api.client() as client:
        assert client.ping() == 'utilisateur-test'
        board = client.create_board('Projet — devops-flow', desc='marker')
        todo = client.create_list(board.id, 'À faire')
        card = client.create_card(todo.id, 'Action', 'Description')
        check = client.create_checklist(card.id, 'Critères de vérification', ('Preuve', 'Revue'))
        assert check == 'k1'
        assert client.list_boards() == (board,)
        assert client.find_boards('marker') == (board,)
        assert client.get_board(board.id) == board
        assert client.get_lists(board.id)[0]['name'] == 'À faire'
        assert client.get_cards(board.id)[0]['desc'] == 'Description'
        assert len(client.get_checklists(card.id)[0]['checkItems']) == 2
    assert client._client.is_closed
    for request in api.calls:
        assert 'API_KEY_SENTINEL' in request.headers['Authorization']
        assert 'TOKEN_SENTINEL' in request.headers['Authorization']
        assert 'TOKEN_SENTINEL' not in str(request.url) and 'API_KEY_SENTINEL' not in str(request.url)
    board_request = next(x for x in api.calls if x.method == 'POST' and x.url.path == '/1/boards')
    posted = parse_qs(board_request.content.decode())
    assert posted['defaultLists'] == ['false'] and posted['prefs_permissionLevel'] == ['private']
    assert 'TOKEN_SENTINEL' not in caplog.text
    assert 'API_KEY_SENTINEL' not in caplog.text


@pytest.mark.parametrize('status,code', [(401, 'unauthorized'), (403, 'forbidden'), (404, 'missing'), (400, 'request'), (302, 'request')])
def test_http_errors_are_neutral_and_not_retried(status, code):
    calls = []
    def request(req):
        calls.append(req)
        return httpx.Response(status, text='SENSITIVE_SERVER_BODY_TOKEN')
    with HttpTrelloClient(TrelloCredentials('a', 'b'), transport=httpx.MockTransport(request)) as client:
        with pytest.raises(TrelloError) as error:
            client.ping()
    assert error.value.code == code and len(calls) == 1
    assert 'SENSITIVE_SERVER' not in str(error.value)


@pytest.mark.parametrize('header,delay', [('7', 7), ('bad', 10), ('-1', 10), ('nan', 10),
                                        (format_datetime(datetime.fromtimestamp(1008, timezone.utc)), 8)])
def test_rate_limit_retry_after(header, delay):
    attempts, sleeps = [], []
    def request(req):
        attempts.append(req)
        return httpx.Response(429, headers={'Retry-After': header}) if len(attempts) == 1 else httpx.Response(200, json={'username': 'ok'})
    with HttpTrelloClient(TrelloCredentials('a', 'b'), transport=httpx.MockTransport(request), sleep=sleeps.append, clock=lambda: 1000) as client:
        assert client.ping() == 'ok'
    assert sleeps == [delay] and len(attempts) == 2


def test_long_rate_limit_is_not_shortened():
    sleeps = []
    with HttpTrelloClient(TrelloCredentials('a', 'b'), clock=lambda: 1000, sleep=sleeps.append,
                         transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={'Retry-After': '120'}))) as client:
        with pytest.raises(TrelloError) as error:
            client.ping()
    assert error.value.retry_at == 1120 and sleeps == []


@pytest.mark.parametrize('failure', ['server', 'network'])
def test_reads_retry_but_uncertain_writes_do_not(failure):
    calls, sleeps = [], []
    def request(req):
        calls.append(req)
        if failure == 'network':
            raise httpx.ReadTimeout('TOKEN_NOT_TO_DISPLAY')
        return httpx.Response(503)
    with HttpTrelloClient(TrelloCredentials('a', 'b'), transport=httpx.MockTransport(request), sleep=sleeps.append) as client:
        with pytest.raises(TrelloError) as error:
            client.ping()
        assert error.value.code == 'unavailable' and len(calls) == 3 and sleeps == [1, 2]
        with pytest.raises(TrelloError) as error:
            client.create_board('Test')
        assert error.value.code == 'uncertain' and len(calls) == 4
        assert 'TOKEN_NOT_TO_DISPLAY' not in str(error.value)


@pytest.mark.parametrize('payload', ['not-json', {}, {'username': None}, {'username': ''}])
def test_invalid_responses(payload):
    response = httpx.Response(200, text=payload) if isinstance(payload, str) else httpx.Response(200, json=payload)
    with HttpTrelloClient(TrelloCredentials('a', 'b'), transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(TrelloError, match='réponse'):
            client.ping()


@pytest.mark.parametrize('url', ['http://trello.com/b/a', 'https://evil.test/b/a', 'https://trello.com.evil/b/a',
                                  'https://secret@trello.com/b/a', 'https://trello.com/b/a?token=secret', 'javascript:evil'])
def test_untrusted_links_rejected(url):
    with pytest.raises(TrelloError):
        trello_url(url)
