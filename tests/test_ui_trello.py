import asyncio

import httpx
import pytest

from devops_flow.diagnostic import build_diagnostic
from devops_flow.integrations.config import TrelloCredentials
from devops_flow.integrations.trello_projection import TrelloProjector
from test_trello import FakeTrello

pytestmark = pytest.mark.nicegui


@pytest.fixture
def saved_trello(store):
    project = store.create_project('Projet Trello')
    diagnostic = store.save_diagnostic(project.id, 'trello-ui', build_diagnostic(answers={}))
    return project, diagnostic


@pytest.fixture
def fake_trello_ui(monkeypatch):
    api = FakeTrello()
    monkeypatch.setattr('devops_flow.ui.trello.load_credentials', lambda: TrelloCredentials('UI_KEY', 'UI_TOKEN'))
    def client(_):
        result = api.client()
        close = result.close
        def closed():
            api.closed_clients += 1
            close()
        result.close = closed
        return result
    monkeypatch.setattr('devops_flow.ui.trello.HttpTrelloClient', client)
    return api


async def test_preview_without_credentials_does_not_block_local_followup(gui, store, saved_trello):
    project, diagnostic = saved_trello
    await gui.open(project.id, "Plan d'action")
    assert not gui.element('trello_ping').enabled
    await gui.click('trello_preview')
    assert 'Aperçu sans appel à Trello' in gui.texts
    assert not gui.element('trello_create').enabled
    assert store.trello_projection(project.id, pending=True) is None
    action = store.diagnostic_state(project.id, diagnostic.id)[1][0]
    await gui.set(f'action_status_{action.id}', 'done')
    await gui.click(f'action_save_{action.id}')
    assert store.diagnostic_state(project.id, diagnostic.id)[1][0].status == 'done'


async def test_ping_preview_create_add_and_recreate(gui, store, saved_trello, fake_trello_ui):
    project, diagnostic = saved_trello
    api = fake_trello_ui
    await gui.open(project.id, "Plan d'action")
    assert api.calls == []
    await gui.click('trello_preview')
    assert api.calls == [] and gui.element('trello_create').enabled
    await gui.click('trello_ping')
    assert 'Connexion vérifiée : utilisateur-test' in gui.texts
    assert all(x.method == 'GET' for x in api.calls)
    await gui.click('trello_create')
    assert len(api.boards) == 1 and len(api.cards) == 5
    assert gui.element('trello_board_link')._props['href'] == 'https://trello.com/b/b1'
    assert gui.element('trello_mode').value == 'append'
    await gui.click('trello_preview')
    assert not gui.element('trello_create').enabled
    assert any('Aucune nouvelle action' in value for value in gui.texts)
    await gui.set('trello_mode', 'recreate')
    await gui.click('trello_preview')
    await gui.click('trello_create')
    assert len(api.boards) == 2 and len(api.cards) == 10
    assert gui.element('trello_mode').value == 'append'
    assert api.closed_clients == 3
    assert 'UI_TOKEN' not in ' '.join(gui.texts)


async def test_obsolete_preview_shows_error_without_network(gui, store, saved_trello, fake_trello_ui):
    project, diagnostic = saved_trello
    await gui.open(project.id, "Plan d'action")
    await gui.click('trello_preview')
    action = store.diagnostic_state(project.id, diagnostic.id)[1][0]
    store.update_action(project.id, diagnostic.id, action.id, status='done', notes='')
    await gui.click('trello_create')
    assert fake_trello_ui.calls == []
    assert any('Actualisez l’aperçu Trello' in value for value in gui.texts)


@pytest.mark.parametrize('code,message', [(401, 'Connexion Trello refusée'), (403, 'Permissions Trello insuffisantes')])
async def test_neutral_connection_error_and_closed_client(gui, saved_trello, fake_trello_ui, code, message):
    project, _ = saved_trello
    api = fake_trello_ui
    api.fail = lambda request, response: httpx.Response(code, text='PRIVATE_RESPONSE')
    await gui.open(project.id, "Plan d'action")
    await gui.click('trello_ping')
    assert any(message in value for value in gui.texts)
    assert 'PRIVATE_RESPONSE' not in ' '.join(gui.texts)
    assert api.closed_clients == 1


async def test_partial_projection_can_resume_from_ui(gui, store, saved_trello, fake_trello_ui):
    project, diagnostic = saved_trello
    api = fake_trello_ui
    failures = []
    def fail(request, response):
        if request.method == 'POST' and request.url.path == '/1/cards' and not failures:
            failures.append(True)
            raise RuntimeError('Interruption simulée')
    api.fail = fail
    with api.client() as client:
        service = TrelloProjector(store, client)
        with pytest.raises(RuntimeError):
            service.project(service.preview(project.id, diagnostic.id))
    api.fail = None
    await gui.open(project.id, "Plan d'action")
    assert gui.element('trello_mode').value == 'resume'
    await gui.click('trello_preview')
    await gui.click('trello_create')
    assert len(api.boards) == 1 and len(api.cards) == 5
    assert store.trello_projection(project.id, pending=True) is None


async def test_rate_limit_displays_resume_date(gui, saved_trello, fake_trello_ui):
    project, _ = saved_trello
    api = fake_trello_ui
    def rate_limit(request):
        if request.method == 'POST':
            return httpx.Response(429, headers={'Retry-After': '120'})
    api.before = rate_limit
    await gui.open(project.id, "Plan d'action")
    await gui.click('trello_preview')
    await gui.click('trello_create')
    assert any('Reprise possible à partir du' in value for value in gui.texts)


async def test_loading_and_double_click_create_single_board(gui, saved_trello, fake_trello_ui):
    project, _ = saved_trello
    api = fake_trello_ui
    def slow(request, response):
        if request.method == 'POST' and request.url.path == '/1/boards':
            import time
            time.sleep(.15)
    api.fail = slow
    await gui.open(project.id, "Plan d'action")
    await gui.click('trello_preview')
    gui.user.find(marker='trello_create').click()
    await asyncio.sleep(.03)
    assert gui.shell.busy and not gui.element('trello_create').enabled
    gui.user.find(marker='trello_create').click()
    await gui.idle()
    assert len(api.boards) == 1 and not gui.shell.busy
