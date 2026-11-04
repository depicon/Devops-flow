from dataclasses import replace
import json
import threading

import httpx
import pytest

from devops_flow.db import ProjectStore
from devops_flow.diagnostic import build_diagnostic
from devops_flow.integrations.projection import DEFAULT_CRITERIA, STATUSES, build_preview
from devops_flow.integrations.trello import TrelloError
from devops_flow.integrations.trello_projection import TrelloProjector
from devops_flow.recommendations import load_catalog
from test_trello import FakeTrello


@pytest.fixture
def scenario(store):
    project = store.create_project('Projet témoin', 'PRIVATE_PROJECT_DESCRIPTION')
    diagnostic = store.save_diagnostic(project.id, 'trello-one', build_diagnostic(answers={}))
    _, actions = store.diagnostic_state(project.id, diagnostic.id)
    for index, action in enumerate(actions):
        store.update_action(project.id, diagnostic.id, action.id,
                            status=tuple(STATUSES)[index % 4], notes='PRIVATE_COMMENT_SENTINEL')
    return project, diagnostic


def test_preview_is_pure_and_uses_frozen_recommendations(store, scenario):
    project, diagnostic = scenario
    before = store.path.read_bytes()
    plan = TrelloProjector(store).preview(project.id, diagnostic.id)
    assert len(plan.cards) == 5 and plan.board_name == 'Projet témoin — devops-flow'
    assert {c.status for c in plan.cards} == set(STATUSES)
    assert all(c.criteria == DEFAULT_CRITERIA for c in plan.cards)
    assert 'PRIVATE_' not in plan.to_json()
    assert store.path.read_bytes() == before
    assert type(plan).from_json(plan.to_json()) == plan


def test_catalog_criteria_version_and_fallback(store, scenario):
    project, diagnostic = scenario
    stored, actions = store.diagnostic_state(project.id, diagnostic.id)
    catalog = load_catalog()
    catalog['rules']['CAL-A']['verification_criteria'] = ['Preuve réelle', 'Relecture', 'Preuve réelle']
    plan = build_preview(project, stored, actions, mode='create', catalog=catalog)
    card = next(c for c in plan.cards if 'Automatisation' in c.name or 'Automation' in c.name)
    assert card.criteria == ('Preuve réelle', 'Relecture')
    catalog['version'] = 'future'
    assert all(c.criteria == DEFAULT_CRITERIA for c in build_preview(project, stored, actions, mode='create', catalog=catalog).cards)


def test_projection_status_order_idempotence_and_preservation(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    snapshot_before = diagnostic.snapshot.to_dict()
    with api.client() as client:
        service = TrelloProjector(store, client)
        plan = service.preview(project.id, diagnostic.id)
        result = service.project(plan)
        assert list(x['name'] for x in api.lists.values()) == list(STATUSES.values())
        assert [x['name'] for x in api.cards.values()] == [c.name for c in plan.cards]
        for local, remote in zip(plan.cards, api.cards.values()):
            assert remote['idList'] == result.lists[local.status].id
        assert len(api.checklists) == len(plan.cards)
        assert all(len(x['checkItems']) == 2 for x in api.checklists.values())
        assert len(store.trello_projected(project.id, store.trello_projection(project.id)['id'])) == len(plan.cards)
        calls = len(api.calls)
        # Un double clic sur l'ancien aperçu ne crée rien.
        with pytest.raises(ValueError):
            service.project(plan)
        assert len(api.calls) == calls
        card = next(iter(api.cards.values()))
        card['name'], card['idList'] = 'Modification dans Trello', result.lists['done'].id
        append = service.preview(project.id, diagnostic.id, 'append')
        assert append.cards == ()
        with pytest.raises(ValueError, match='Aucune nouvelle'):
            service.project(append)
        assert len(api.boards) == 1 and card['name'] == 'Modification dans Trello'
    assert store.get_diagnostic(project.id, diagnostic.id).snapshot.to_dict() == snapshot_before
    assert 'TOKEN_SENTINEL' not in store.path.read_bytes().decode('utf-8', errors='ignore')
    posted = ' '.join(req.content.decode() for req in api.calls)
    assert 'PRIVATE_' not in posted


def test_new_diagnostic_appends_distinct_action_ids_and_recreate(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    with api.client() as client:
        service = TrelloProjector(store, client)
        first = service.project(service.preview(project.id, diagnostic.id))
        second = store.save_diagnostic(project.id, 'trello-two', build_diagnostic(answers={}))
        plan = service.preview(project.id, second.id, 'append')
        assert len(plan.cards) == 5
        service.project(plan)
        assert len(api.boards) == 1 and len(api.cards) == 10
        # Une carte supprimée manuellement n'est pas recréée par l'ajout.
        del api.cards[first.cards[0].id]
        assert not service.preview(project.id, diagnostic.id, 'append').cards
        result = service.project(service.preview(project.id, second.id, 'recreate'))
        assert result.board.id != first.board.id and len(api.boards) == 2 and len(result.cards) == 5


@pytest.mark.parametrize('stage', ['/1/boards', '/1/lists', '/1/cards', '/1/checklists', '/checkItems'])
def test_response_lost_reconciles_without_duplicate(store, scenario, stage):
    project, diagnostic = scenario
    api, failures = FakeTrello(), []
    def fail(request, response):
        if request.method == 'POST' and request.url.path.endswith(stage) and not failures:
            failures.append(request)
            raise httpx.ReadTimeout('SENSITIVE_TRACE')
    api.fail = fail
    with api.client() as client:
        result = TrelloProjector(store, client, sleep=lambda _: None).project(TrelloProjector(store).preview(project.id, diagnostic.id))
    assert len(failures) == 1 and len(api.boards) == 1 and len(api.lists) == 4
    assert len(result.cards) == len(api.cards) == len(api.checklists) == 5
    assert all(len(c['checkItems']) == 2 for c in api.checklists.values())


def test_crash_partial_checklist_resumes_after_restart(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    failures = []
    def crash(request, response):
        if request.method == 'POST' and request.url.path.endswith('/checkItems') and not failures:
            failures.append(request)
            raise RuntimeError('Simulated process crash after first item')
    api.fail = crash
    with api.client() as client:
        service = TrelloProjector(store, client)
        with pytest.raises(RuntimeError):
            service.project(service.preview(project.id, diagnostic.id))
    assert len(api.boards) == len(api.cards) == len(api.checklists) == 1
    api.fail = None
    restarted = ProjectStore(store.path)
    restarted.initialize()
    # Une modification locale n'altère pas l'aperçu enregistré de la reprise.
    action = store.diagnostic_state(project.id, diagnostic.id)[1][0]
    store.update_action(project.id, diagnostic.id, action.id, status='dismissed', notes='')
    with api.client() as client:
        service = TrelloProjector(restarted, client)
        service.project(service.preview(project.id, diagnostic.id))
    assert len(api.boards) == 1 and len(api.cards) == 5
    assert all(len(c['checkItems']) == 2 for c in api.checklists.values())


def test_legacy_pending_board_resumes_with_original_markers(store, scenario, monkeypatch):
    project, diagnostic = scenario
    api = FakeTrello()
    failures = []

    def crash(request, response):
        if request.method == 'POST' and request.url.path.endswith('/boards') and not failures:
            failures.append(request)
            raise RuntimeError('Crash après création du board historique')

    api.fail = crash
    with api.client() as client:
        service = TrelloProjector(store, client)
        # Simule la création par l'ancienne version, puis restaure le constructeur courant.
        with monkeypatch.context() as previous_version:
            previous_version.setattr('devops_flow.integrations.trello_projection.build_preview',
                                     lambda *args, **kwargs: replace(build_preview(*args, **kwargs),
                                                                   board_name=project.name + ' — DevOps Assist'))
            legacy_plan = service.preview(project.id, diagnostic.id)
            with pytest.raises(RuntimeError):
                service.project(legacy_plan)
        pending = service.preview(project.id, diagnostic.id)
    assert len(api.boards) == 1
    board = next(iter(api.boards.values()))
    assert board['desc'] == f'DevOps-Assist-Projection: {pending.projection_id}'
    api.fail = None
    with api.client() as client:
        service = TrelloProjector(store, client, sleep=lambda _: None)
        resumed = service.preview(project.id, diagnostic.id)
        assert resumed.to_json() == pending.to_json()
        service.project(resumed)
    assert len(api.boards) == 1 and len(api.cards) == len(legacy_plan.cards)
    assert board['name'] == project.name + ' — DevOps Assist'
    assert all('DevOps-Assist-Action: ' in card['desc'] for card in api.cards.values())


def test_uncertain_creation_suspends_and_can_be_recreated(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    def fail(request, response):
        if request.method == 'POST' and request.url.path == '/1/boards':
            api.boards.clear()  # Impossible de retrouver le résultat côté serveur.
            return httpx.Response(503)
    api.fail = fail
    with api.client() as client:
        service = TrelloProjector(store, client, sleep=lambda _: None)
        for _ in range(2):
            with pytest.raises(TrelloError) as error:
                service.project(service.preview(project.id, diagnostic.id))
            assert error.value.code == 'uncertain'
        assert sum(r.method == 'POST' for r in api.calls) == 1
        api.fail = None
        service.project(service.preview(project.id, diagnostic.id, 'recreate'))
    assert store.trello_projection(project.id)['state'] == 'ready'


def test_recreation_crash_keeps_old_active_and_resumes(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    with api.client() as client:
        service = TrelloProjector(store, client)
        first = service.project(service.preview(project.id, diagnostic.id))
        def crash(request, response):
            if request.method == 'POST' and request.url.path == '/1/boards':
                raise RuntimeError('crash')
        api.fail = crash
        with pytest.raises(RuntimeError):
            service.project(service.preview(project.id, diagnostic.id, 'recreate'))
        assert json.loads(store.trello_projection(project.id)['board_json'])['id'] == first.board.id
        api.fail = None
        service.project(service.preview(project.id, diagnostic.id))
    assert len(api.boards) == 2
    assert json.loads(store.trello_projection(project.id)['board_json'])['id'] != first.board.id


@pytest.mark.parametrize('change', ['status', 'order', 'name'])
def test_obsolete_preview_refused_before_network(store, scenario, change):
    project, diagnostic = scenario
    api = FakeTrello()
    with api.client() as client:
        service = TrelloProjector(store, client)
        plan = service.preview(project.id, diagnostic.id)
        action = store.diagnostic_state(project.id, diagnostic.id)[1][0]
        if change == 'status':
            store.update_action(project.id, diagnostic.id, action.id, status='done', notes='')
        elif change == 'order':
            store.move_action(project.id, diagnostic.id, action.id, 1)
        else:
            store.update_project(project.id, 'Renommé', '', profile_slug='baseline', profile_version=1)
        with pytest.raises(ValueError, match='plan a changé'):
            service.project(plan)
    assert api.calls == [] and store.trello_projection(project.id, pending=True) is None


def test_lock_shared_between_sessions_and_connections(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    started, release = threading.Event(), threading.Event()
    def block(request, response):
        if request.method == 'POST' and request.url.path == '/1/boards':
            started.set()
            assert release.wait(5)
    api.fail = block
    errors = []
    with api.client() as client:
        service = TrelloProjector(store, client)
        plan = service.preview(project.id, diagnostic.id)
        def first():
            try:
                service.project(plan)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=first)
        thread.start()
        try:
            assert started.wait(5)
            other = TrelloProjector(ProjectStore(store.path), client)
            with pytest.raises(ValueError, match='déjà en cours'):
                other.project(plan)
        finally:
            release.set()
            thread.join(5)
    assert not thread.is_alive() and not errors and len(api.boards) == 1


@pytest.mark.parametrize('target', ['board', 'list', 'missing'])
def test_unavailable_board_or_list_stops_append(store, scenario, target):
    project, diagnostic = scenario
    api = FakeTrello()
    with api.client() as client:
        service = TrelloProjector(store, client)
        service.project(service.preview(project.id, diagnostic.id))
        second = store.save_diagnostic(project.id, 'second', build_diagnostic(answers={}))
        if target == 'board':
            next(iter(api.boards.values()))['closed'] = True
        elif target == 'list':
            next(iter(api.lists.values()))['closed'] = True
        else:
            api.boards.clear()
        calls = sum(r.method == 'POST' for r in api.calls)
        with pytest.raises(TrelloError):
            service.project(service.preview(project.id, second.id, 'append'))
        assert sum(r.method == 'POST' for r in api.calls) == calls


def test_migration_repeated_and_project_isolation(store, scenario):
    project, diagnostic = scenario
    before = diagnostic.snapshot.to_dict()
    store.initialize()
    store.initialize()
    assert store.get_diagnostic(project.id, diagnostic.id).snapshot.to_dict() == before
    other = store.create_project('Autre')
    with pytest.raises(ValueError):
        TrelloProjector(store).preview(other.id, diagnostic.id)
    with pytest.raises(ValueError):
        TrelloProjector(store).preview(project.id, diagnostic.id, 'invalid')
    with store._connection() as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('status,code', [(401, 'unauthorized'), (403, 'forbidden'), (429, 'rate_limit')])
def test_rejected_write_can_resume_after_correction(store, scenario, status, code):
    project, diagnostic = scenario
    api = FakeTrello()
    now = [1000.0]
    api.before = lambda req: httpx.Response(status, headers={'Retry-After': '120'}) if req.method == 'POST' else None
    with api.client(clock=lambda: now[0]) as client:
        service = TrelloProjector(store, client, sleep=lambda _: None, clock=lambda: now[0])
        with pytest.raises(TrelloError) as error:
            service.project(service.preview(project.id, diagnostic.id))
        assert error.value.code == code and not api.boards
        api.before = None
        if status == 429:
            calls = len(api.calls)
            with pytest.raises(TrelloError) as error:
                service.project(service.preview(project.id, diagnostic.id))
            assert error.value.code == 'rate_limit' and len(api.calls) == calls
            now[0] += 121
        service.project(service.preview(project.id, diagnostic.id))
        assert len(api.boards) == 1 and len(api.cards) == 5


def test_board_with_same_name_is_not_adopted(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    with api.client() as client:
        unrelated = client.create_board(project.name + ' — devops-flow', desc='Unrelated board')
        service = TrelloProjector(store, client)
        result = service.project(service.preview(project.id, diagnostic.id))
    assert len(api.boards) == 2 and result.board.id != unrelated.id


def test_ambiguous_recovery_stops_without_new_post(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    def crash(request, response):
        if request.method == 'POST' and request.url.path == '/1/boards':
            original = next(iter(api.boards.values()))
            api.boards['duplicate'] = {**original, 'id': 'duplicate', 'url': 'https://trello.com/b/duplicate'}
            raise RuntimeError('Process interrupted')
    api.fail = crash
    with api.client() as client:
        service = TrelloProjector(store, client, sleep=lambda _: None)
        with pytest.raises(RuntimeError):
            service.project(service.preview(project.id, diagnostic.id))
        api.fail = None
        with pytest.raises(TrelloError) as error:
            service.project(service.preview(project.id, diagnostic.id))
    assert error.value.code == 'ambiguous'
    assert sum(x.method == 'POST' for x in api.calls) == 1


def test_completed_checked_item_is_preserved_during_resume(store, scenario):
    project, diagnostic = scenario
    api = FakeTrello()
    def crash(request, response):
        if request.method == 'POST' and request.url.path.endswith('/checkItems'):
            item = next(iter(api.checklists.values()))['checkItems'][0]
            item['state'] = 'complete'
            raise RuntimeError('Interrupted after item created and checked in Trello')
    api.fail = crash
    with api.client() as client:
        service = TrelloProjector(store, client)
        with pytest.raises(RuntimeError):
            service.project(service.preview(project.id, diagnostic.id))
        api.fail = None
        service.project(service.preview(project.id, diagnostic.id))
    first = next(iter(api.checklists.values()))['checkItems']
    assert len(first) == 2 and first[0]['state'] == 'complete'


def test_upgrade_from_database_without_trello_tables_preserves_history(store, scenario):
    project, diagnostic = scenario
    with store._connection() as connection:
        before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                  for table in ('projects', 'diagnostics', 'actions')}
        for table in ('trello_operations', 'trello_projected_actions', 'trello_projections'):
            connection.execute(f'DROP TABLE {table}')
    upgraded = ProjectStore(store.path)
    upgraded.initialize()
    upgraded.initialize()
    with upgraded._connection() as connection:
        after = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                 for table in before}
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    assert after == before
