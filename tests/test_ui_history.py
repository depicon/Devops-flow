import json
import sqlite3
import pytest
from nicegui import ui
from devops_flow.calms import load_questionnaire
from devops_flow.diagnostic import build_diagnostic
from devops_flow.reports import render_json, render_markdown


@pytest.fixture
def saved(store):
    p = store.create_project('Projet A')
    return store.save_diagnostic(p.id, 'one', build_diagnostic(answers={}))


@pytest.fixture
async def downloads(gui, monkeypatch):
    captured = {}
    original = gui.user.download.content
    def capture(data, filename=None, media_type=''):
        captured[filename] = {'data': data, 'mime': media_type, 'file_name': filename}
        return original(data, filename, media_type)
    monkeypatch.setattr(gui.user.download, 'content', capture)
    return captured


@pytest.mark.parametrize('page', ["Plan d'action", 'Historique / Rapports'])
async def test_project_without_diagnostic_has_guidance(gui, store, page):
    p = store.create_project('Vide')
    await gui.open(p.id, page)
    await gui.user.should_see('Aucun diagnostic enregistré')


async def test_ui_saves_all_statuses_comment_and_order_then_reopens(gui, store, saved):
    before, actions = store.diagnostic_state(saved.project_id, saved.id)
    action = actions[1]
    await gui.open(saved.project_id, "Plan d'action")
    for status in ('in_progress', 'done', 'dismissed', 'todo'):
        await gui.set(f'action_status_{action.id}', status)
        await gui.set(f'action_notes_{action.id}', 'Commentaire local **littéral** é 🧭')
        await gui.click(f'action_save_{action.id}')
        updated = store.diagnostic_state(saved.project_id, saved.id)[1][1]
        assert updated.status == status
        assert updated.notes == 'Commentaire local **littéral** é 🧭'
    await gui.click(f'action_up_{action.id}')
    assert store.diagnostic_state(saved.project_id, saved.id)[1][0].id == action.id
    await gui.open(saved.project_id, "Plan d'action")
    assert gui.element(f'action_notes_{action.id}').value == updated.notes
    assert not gui.element(f'action_up_{action.id}').enabled
    assert not gui.element(f'action_down_{actions[-1].id}').enabled
    await gui.click(f'action_down_{action.id}')
    assert store.diagnostic_state(saved.project_id, saved.id)[1][1].id == action.id
    assert store.get_diagnostic(saved.project_id, saved.id) == before


async def test_old_diagnostic_selection_without_engine_and_independent_followup(gui, store, saved, monkeypatch):
    second = store.save_diagnostic(saved.project_id, 'two', build_diagnostic(answers={'C1': 3, 'C2': 3}))
    def forbidden(*args, **kwargs):
        pytest.fail('Historique : moteur ou scanner appelé')
    monkeypatch.setattr('devops_flow.diagnostic.build_diagnostic', forbidden)
    monkeypatch.setattr('devops_flow.diagnostic.calculate_scores', forbidden)
    monkeypatch.setattr('devops_flow.scanner.scan_local', forbidden)
    monkeypatch.setattr('devops_flow.scanner.scan_zip', forbidden)
    await gui.open(saved.project_id, 'Historique / Rapports')
    selector = f'history_diagnostic_{saved.project_id}'
    assert gui.element(selector).value == second.id
    await gui.set(selector, saved.id)
    await gui.user.should_see(saved.id)
    assert gui.session.report(saved.project_id, saved.id).to_dict()['diagnostic_id'] == saved.id
    await gui.nav("Plan d'action")
    await gui.set(f'roadmap_diagnostic_{saved.project_id}', saved.id)
    action = store.diagnostic_state(saved.project_id, saved.id)[1][0]
    await gui.set(f'action_status_{action.id}', 'done')
    await gui.click(f'action_save_{action.id}')
    assert all(a.status == 'todo' for a in store.diagnostic_state(saved.project_id, second.id)[1])
    assert store.get_diagnostic(saved.project_id, saved.id) == saved


async def test_report_buttons_previews_share_bytes_timestamp_and_refresh_after_followup(gui, store, saved, downloads):
    await gui.open(saved.project_id, 'Historique / Rapports')
    original = gui.session.report(saved.project_id, saved.id)
    md_key, js_key = f'download_md_{saved.id}', f'download_json_{saved.id}'
    await gui.click(md_key)
    await gui.click(js_key)
    md = next(v for k, v in downloads.items() if k.endswith('.md'))
    js = next(v for k, v in downloads.items() if k.endswith('.json'))
    assert md['data'] == render_markdown(original).encode('utf-8')
    assert js['data'] == render_json(original).encode('utf-8')
    assert saved.project_id in js['file_name']
    assert md['mime'].startswith('text/markdown') and js['mime'] == 'application/json'
    assert gui.element('report_json').content.strip() == render_json(original).strip()
    assert gui.element('report_markdown').content.strip() == render_markdown(original).strip()
    assert gui.session.report(saved.project_id, saved.id) == original
    action = store.diagnostic_state(saved.project_id, saved.id)[1][0]
    store.update_action(saved.project_id, saved.id, action.id, status='done', notes='FAKE_SECRET_COMMENT')
    await gui.nav('Projets')
    await gui.nav('Historique / Rapports')
    refreshed = gui.session.report(saved.project_id, saved.id)
    assert refreshed != original
    assert refreshed.to_dict()['actions'][0]['status'] == 'done'
    await gui.click(js_key)
    await gui.click(md_key)
    assert json.loads(js['data'])['exported_at'] == original.to_dict()['exported_at']
    latest_js = next(v for k, v in downloads.items() if k.endswith('.json'))
    latest_md = next(v for k, v in downloads.items() if k.endswith('.md'))
    assert json.loads(latest_js['data'])['exported_at'] == refreshed.to_dict()['exported_at']
    assert refreshed.to_dict()['exported_at'] in latest_md['data'].decode()
    assert 'FAKE_SECRET' not in latest_js['data'].decode()
    await gui.click(f'report_refresh_{saved.id}')
    assert gui.session.report(saved.project_id, saved.id).to_dict()['exported_at'] != refreshed.to_dict()['exported_at']


@pytest.mark.parametrize('page', ["Plan d'action", 'Historique / Rapports'])
async def test_project_switch_does_not_leak_other_diagnostics(gui, store, saved, page):
    other = store.create_project('Projet B')
    await gui.open(saved.project_id, page)
    await gui.nav('Projets')
    await gui.click(f'open_{other.id}')
    await gui.nav(page)
    await gui.user.should_not_see(saved.id)
    await gui.user.should_see('Aucun diagnostic enregistré')


@pytest.mark.parametrize('page', ["Plan d'action", 'Historique / Rapports'])
async def test_favorable_diagnostic_has_no_editable_actions(gui, store, page):
    p = store.create_project('Favorable')
    store.save_diagnostic(p.id, 'favorable', build_diagnostic(answers={q: 3 for q in load_questionnaire().question_ids}))
    await gui.open(p.id, page)
    assert not any(isinstance(e, ui.textarea) for e in gui.user.client.elements.values())
    await gui.user.should_see('Aucune action')


@pytest.mark.parametrize('page', ["Plan d'action", 'Historique / Rapports'])
async def test_corrupt_history_shows_neutral_error_and_other_diagnostic_still_opens(gui, store, saved, page):
    second = store.save_diagnostic(saved.project_id, 'two', build_diagnostic(answers={}))
    with sqlite3.connect(store.path) as c:
        c.execute('UPDATE diagnostics SET snapshot_json=? WHERE id=?', ('FAKE_SECRET_CORRUPT', second.id))
    await gui.open(saved.project_id, page)
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)
    space = 'history' if page == 'Historique / Rapports' else 'roadmap'
    await gui.set(f'{space}_diagnostic_{saved.project_id}', saved.id)
    await gui.user.should_see(saved.id)
    assert not any('incompatible' in t for t in gui.texts)


async def test_minimal_guided_journey_to_followup_and_both_report_payloads(gui, store, downloads):
    await gui.open()
    await gui.click('project_new')
    await gui.set('project_name', 'Parcours complet')
    await gui.click('project_create')
    pid = gui.session.active_project_id
    await gui.nav('Diagnostic guidé')
    await gui.click(f'calms_save_{pid}')
    await gui.set(f'include_calms_{pid}', True)
    await gui.click(f'diagnostic_finalize_{pid}')
    saved = store.latest_diagnostic(pid)
    await gui.nav("Plan d'action")
    action = store.diagnostic_state(pid, saved.id)[1][0]
    await gui.set(f'action_status_{action.id}', 'in_progress')
    await gui.click(f'action_save_{action.id}')
    await gui.nav('Historique / Rapports')
    await gui.click(f'download_json_{saved.id}')
    await gui.click(f'download_md_{saved.id}')
    js = next(v['data'] for k,v in downloads.items() if k.endswith('.json'))
    md = next(v['data'] for k,v in downloads.items() if k.endswith('.md'))
    assert json.loads(js)['actions'][0]['status'] == 'in_progress'
    assert 'En cours' in md.decode()
