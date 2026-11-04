from pathlib import Path
import pytest
from devops_flow.diagnostic import build_diagnostic


@pytest.mark.parametrize('page,key', [('Diagnostic guidé', 'diagnostic_projects'), ("Plan d'action", 'roadmap_projects'), ('Historique / Rapports', 'history_projects')])
async def test_empty_page_action_returns_to_projects(gui, page, key):
    await gui.open(page=page)
    await gui.click(key)
    assert gui.session.page == 'Projets'
    assert gui.element('project_new')


async def test_project_shortcut_retains_scan_context(gui, store):
    p = store.create_project('Reprise')
    await gui.open(p.id)
    await gui.click('projects_diagnostic')
    await gui.local(p.id, Path(__file__).parent/'fixtures/healthy_project')
    original = gui.session.project().scan
    await gui.nav('Projets')
    await gui.click('projects_diagnostic')
    assert gui.session.project().scan == original
    assert gui.element(f'scan_mode_{p.id}').value == 'Dossier local'


async def test_followup_confirmation_survives_rerun_and_counts_update(gui, store):
    p = store.create_project('Suivi')
    saved = store.save_diagnostic(p.id, 'one', build_diagnostic(answers={}))
    action = store.diagnostic_state(p.id, saved.id)[1][0]
    await gui.open(p.id, "Plan d'action")
    await gui.set(f'action_status_{action.id}', 'done')
    await gui.click(f'action_save_{action.id}')
    await gui.user.should_see('Suivi enregistré')
    assert store.diagnostic_state(p.id, saved.id)[1][0].status == 'done'
    await gui.nav('Projets')
    await gui.nav("Plan d'action")
    await gui.user.should_not_see('Suivi enregistré')


async def test_project_free_text_stays_literal_in_common_header(gui, store):
    marker = '<img src=x onerror="alert(1)"> **projet**'
    p = store.create_project(marker, '[texte](https://example.invalid)')
    await gui.open(p.id)
    await gui.click('projects_diagnostic')
    await gui.user.should_see(marker)
    from nicegui import ui
    assert any(isinstance(e, ui.label) and e.text == marker for e in gui.user.client.elements.values())
    assert not any(isinstance(e, ui.html) and marker in e.content for e in gui.user.client.elements.values())


@pytest.mark.parametrize('space,page', [('roadmap', "Plan d'action"), ('history', 'Historique / Rapports')])
async def test_result_shortcuts_select_displayed_diagnostic_without_new_submission(gui, store, space, page):
    p = store.create_project('Résultat')
    old = store.save_diagnostic(p.id, 'one', build_diagnostic(answers={}))
    saved = store.save_diagnostic(p.id, 'two', build_diagnostic(answers={'C1': 3}))
    await gui.open(p.id, 'Diagnostic guidé')
    gui.session.project().selected[page] = old.id
    await gui.click(f'results_{space}_{p.id}')
    assert gui.session.page == page
    assert gui.element(f'{space}_diagnostic_{p.id}').value == saved.id
    assert store.get_diagnostic(p.id, saved.id) == saved
    assert len(store.list_diagnostics(p.id)) == 2
