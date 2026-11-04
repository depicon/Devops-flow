import pytest
from nicegui import ui

from devops_flow.diagnostic import build_diagnostic
from devops_flow.diagram import build_flow
from devops_flow.ui.flow import readable_mermaid

def display_mermaid(snapshot):
    return readable_mermaid(build_flow(snapshot))


@pytest.fixture
def saved(store):
    project = store.create_project('Flux A')
    return store.save_diagnostic(project.id, 'one', build_diagnostic(answers={}))


@pytest.mark.parametrize('page', ['Diagnostic guidé', 'Historique / Rapports'])
async def test_flow_element_panel_selection_and_legend(gui, saved, page):
    await gui.open(saved.project_id, page)
    key = saved.project_id if page == 'Diagnostic guidé' else f'history_{saved.project_id}_{saved.id}'
    chart = gui.element(f'flow_diagram_{key}')
    assert isinstance(chart, ui.mermaid)
    assert chart.content == display_mermaid(saved.snapshot)
    assert chart._props['config']['securityLevel'] == 'strict'
    await gui.user.should_see(marker=f'flow_trace_{key}')
    await gui.set(f'flow_node_{key}', 'test')
    assert gui.element(f'flow_status_{key}').text == 'Tests · À confirmer'
    await gui.click(f'flow_legend_{key}_unknown')
    assert 'class code,test,deploy,observe,feedback selected' in gui.element(f'flow_diagram_{key}').content
    await gui.click(f'flow_legend_{key}_unknown')
    assert gui.element(f'flow_diagram_{key}').content == chart.content
    gui.user.find(marker=f'flow_diagram_{key}').trigger('node_click', 'c441_mermaid-flowchart-operate-5')
    await gui.idle()
    assert gui.element(f'flow_node_{key}').value == 'operate'


async def test_source_opens_finding_and_resets_filters(gui, saved):
    key = saved.project_id
    await gui.open(key, 'Diagnostic guidé')
    await gui.set(f'findings_status_{key}', 'risk')
    await gui.set(f'findings_source_{key}', 'observé')
    await gui.set(f'flow_node_{key}', 'test')
    await gui.click(f'flow_source_{key}_0')
    assert gui.element(f'findings_status_{key}').value == 'Tous'
    assert gui.element(f'findings_source_{key}').value == 'Toutes'
    assert gui.element(f'finding_{key}_F-CAL-A').value is True


async def test_historical_source_keeps_exact_diagnostic_and_project(gui, saved, store):
    second = store.save_diagnostic(saved.project_id, 'two', build_diagnostic(answers={'A2': 3}))
    await gui.open(saved.project_id, 'Historique / Rapports')
    await gui.set(f'history_diagnostic_{saved.project_id}', saved.id)
    key = f'history_{saved.project_id}_{saved.id}'
    await gui.set(f'flow_node_{key}', 'test')
    await gui.click(f'flow_source_{key}_0')
    assert gui.session.page == 'Diagnostic guidé'
    assert gui.element(f'results_diagnostic_{saved.project_id}').value == saved.id
    assert gui.element(f'finding_{saved.project_id}_F-CAL-A').value is True
    assert gui.element(f'flow_diagram_{saved.project_id}').content == display_mermaid(saved.snapshot)
    other = store.create_project('Flux B')
    await gui.nav('Projets')
    await gui.click(f'open_{other.id}')
    await gui.nav('Diagnostic guidé')
    assert not any(isinstance(e, ui.mermaid) for e in gui.user.client.elements.values())
    assert store.get_diagnostic(saved.project_id, second.id) == second


async def test_mermaid_failure_keeps_trace_and_allows_retry(gui, saved):
    await gui.open(saved.project_id, 'Diagnostic guidé')
    key = saved.project_id
    gui.user.find(marker=f'flow_diagram_{key}').trigger('error', {'message': 'PRIVATE_ERROR'})
    await gui.idle()
    await gui.user.should_see(marker=f'flow_error_{key}')
    assert 'PRIVATE_ERROR' not in ' '.join(gui.texts)
    await gui.set(f'flow_node_{key}', 'test')
    assert gui.element(f'flow_status_{key}').text == 'Tests · À confirmer'
    await gui.click(f'flow_retry_{key}')
    assert gui.element(f'flow_diagram_{key}').visible


async def test_corrupt_latest_does_not_block_an_old_source_target(gui, saved, store):
    import sqlite3
    latest = store.save_diagnostic(saved.project_id, 'corrupt', build_diagnostic(answers={}))
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE diagnostics SET snapshot_json=? WHERE id=?', ('PRIVATE_CORRUPT', latest.id))
    await gui.open(saved.project_id, 'Diagnostic guidé')
    assert 'PRIVATE_CORRUPT' not in ' '.join(gui.texts)
    await gui.set(f'results_diagnostic_{saved.project_id}', saved.id)
    assert gui.element(f'flow_diagram_{saved.project_id}').content == display_mermaid(saved.snapshot)
