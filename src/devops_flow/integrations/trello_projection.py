"""Orchestration Trello reprenable ; aucune dépendance NiceGUI."""
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time

from devops_flow.integrations.projection import ActionPlanPreview, CHECKLIST_NAME, STATUSES, build_preview
from devops_flow.integrations.trello import (
    TrelloBoard, TrelloCard, TrelloList, TrelloProjection, TrelloError,
    board_from, card_from, identifier,
)
from devops_flow.recommendations import load_catalog

_locks_guard = threading.Lock()
_locks: dict[tuple[str, str], threading.Lock] = {}


def _lock(store, project_id):
    with _locks_guard:
        return _locks.setdefault((str(Path(store.path).resolve()), project_id), threading.Lock())


class TrelloProjector:
    def __init__(self, store, client=None, *, sleep=time.sleep, clock=time.time):
        self.store, self.client = store, client
        self.sleep, self.clock = sleep, clock

    def preview(self, project_id, diagnostic_id, mode='create'):
        if mode not in ('create', 'append', 'recreate'):
            raise ValueError('Le mode de projection Trello est invalide.')
        project = self.store.get_project(project_id)
        if project is None:
            raise ValueError('Le projet demandé n’est pas disponible.')
        pending = self.store.trello_projection(project_id, pending=True)
        if pending and mode != 'recreate':
            return ActionPlanPreview.from_json(pending['plan_json'])
        active = self.store.trello_projection(project_id)
        if active and mode == 'create':
            raise ValueError('Un board existe déjà. Choisissez l’ajout des nouvelles actions ou la recréation.')
        if mode == 'append' and not active:
            raise ValueError('Aucun board Trello n’est associé à ce projet.')
        diagnostic, actions = self.store.diagnostic_state(project_id, diagnostic_id)
        projected = self.store.trello_projected(project_id, active['id']) if active and mode == 'append' else {}
        return build_preview(project, diagnostic, actions, mode=mode,
                             projection_id=active['id'] if active and mode == 'append' else None,
                             projected=projected, catalog=load_catalog())

    @staticmethod
    def _one(matches):
        if len(matches) > 1:
            raise TrelloError('ambiguous')
        return matches[0] if matches else None

    def _step(self, plan, step, payload, create, find, progress):
        record, fresh = self.store.trello_operation(plan.projection_id, step, payload)
        if record['state'] == 'done':
            return json.loads(record['result_json'])
        if record['retry_at'] and record['retry_at'] > self.clock():
            raise TrelloError('rate_limit', retry_at=record['retry_at'])
        progress(step)
        # Une opération refusée explicitement (401/403/429...) est répétable.
        rejected = record.get('retry_at') is not None
        if fresh or rejected:
            try:
                result = create()
            except TrelloError as error:
                uncertain = error.code in ('uncertain', 'response')
                self.store.trello_checkpoint(plan.projection_id, step, uncertain=True,
                                            retry_at=error.retry_at if error.retry_at is not None else (None if uncertain else 0))
                if not uncertain:
                    raise
            else:
                self.store.trello_checkpoint(plan.projection_id, step, result=result)
                return result
        # Une réponse perdue n'autorise jamais un nouveau POST à l'aveugle.
        for attempt in range(3):
            if attempt:
                self.sleep(2 ** (attempt - 1))
            result = self._one(find())
            if result is not None:
                self.store.trello_checkpoint(plan.projection_id, step, result=result)
                return result
        raise TrelloError('uncertain')

    def project(self, preview, progress=None):
        if self.client is None:
            raise ValueError('Configurez les identifiants Trello avant de créer le board.')
        mutex = _lock(self.store, preview.project_id)
        if not mutex.acquire(blocking=False):
            raise ValueError('Une projection Trello est déjà en cours pour ce projet.')
        try:
            current = self.preview(preview.project_id, preview.diagnostic_id, preview.mode)
            if current != preview:
                raise ValueError('Le plan a changé. Actualisez l’aperçu Trello avant de poursuivre.')
            if not preview.cards:
                raise ValueError('Aucune nouvelle action à projeter.')
            pending = self.store.trello_projection(preview.project_id, pending=True)
            plan = preview if pending and preview.mode != 'recreate' else self.store.trello_start(preview)
            return self._project(plan, progress or (lambda _: None))
        finally:
            mutex.release()

    def _project(self, plan, progress):
        marker = f'DevOps-Assist-Projection: {plan.projection_id}'
        def step(key, payload, create, find):
            return self._step(plan, key, payload, create, find, progress)
        board = board_from(step('board', {'name': plan.board_name, 'desc': marker},
                                lambda: asdict(self.client.create_board(plan.board_name, desc=marker)),
                                lambda: [asdict(b) for b in self.client.find_boards(marker)]))
        self.client.get_board(board.id)  # Accès/archivage contrôlés avant tout ajout.
        lists = {}
        def matching_lists(name):
            results = []
            for item in self.client.get_lists(board.id):
                if item.get('name') == name:
                    if item.get('closed') is not False:
                        raise TrelloError('closed')
                    results.append({'id': identifier(item.get('id')), 'name': name})
            return results
        for status, name in STATUSES.items():
            data = step('list:' + status, {'board': board.id, 'name': name},
                        lambda name=name: asdict(self.client.create_list(board.id, name)),
                        lambda name=name: matching_lists(name))
            lists[status] = TrelloList(**data)
        actual_lists = {x.get('id'): x for x in self.client.get_lists(board.id)}
        for target in lists.values():
            if target.id not in actual_lists:
                raise TrelloError('missing')
            if actual_lists[target.id].get('closed') is not False:
                raise TrelloError('closed')
        completed = self.store.trello_projected(plan.project_id, plan.projection_id)
        cards = []
        for index, card in enumerate(plan.cards):
            progress(f'Carte {index + 1}/{len(plan.cards)}')
            if card.action_id in completed:
                cards.append(card_from(completed[card.action_id]))
                continue
            card_marker = f'DevOps-Assist-Action: {plan.projection_id}/{card.action_id}'
            desc = card.description + '\n\n' + card_marker
            result = step('card:' + card.action_id, {'list': lists[card.status].id, 'name': card.name, 'desc': desc},
                          lambda: asdict(self.client.create_card(lists[card.status].id, card.name, desc)),
                          lambda: [asdict(card_from(x)) for x in self.client.get_cards(board.id)
                                   if isinstance(x.get('desc'), str) and card_marker in x['desc'].splitlines()])
            remote = card_from(result)
            checklist = step('checklist:' + card.action_id, {'card': remote.id, 'name': CHECKLIST_NAME},
                             lambda: {'id': self.client.create_empty_checklist(remote.id, CHECKLIST_NAME)},
                             lambda: [{'id': identifier(x.get('id'))} for x in self.client.get_checklists(remote.id)
                                      if x.get('name') == CHECKLIST_NAME])['id']
            def find_item(name):
                matching = [x for x in self.client.get_checklists(remote.id) if x.get('id') == checklist]
                if not matching:
                    raise TrelloError('missing')
                items = matching[0].get('checkItems')
                if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
                    raise TrelloError('response')
                return [{'id': identifier(x.get('id'))} for x in items if x.get('name') == name]
            for number, name in enumerate(card.criteria):
                step(f'item:{card.action_id}:{number}', {'checklist': checklist, 'name': name},
                     lambda name=name: {'id': self.client.create_checkitem(checklist, name)},
                     lambda name=name: find_item(name))
            self.store.trello_card_complete(plan, card, result)
            cards.append(remote)
        self.store.trello_finish(plan)
        return TrelloProjection(board, lists, tuple(cards))
