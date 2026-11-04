from pathlib import Path
import sqlite3
from devops_flow.calms import load_questionnaire
from devops_flow.diagnostic import build_diagnostic

FIXTURES = Path(__file__).parent / 'fixtures'


def count(store):
    with sqlite3.connect(store.path) as c:
        return c.execute('SELECT count(*) FROM diagnostics').fetchone()[0]


async def test_no_sources_refused_and_nothing_persisted(gui, store):
    p = store.create_project('Sans source')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.click(f'diagnostic_finalize_{p.id}')
    await gui.user.should_see('questionnaire ou réalisez un scan')
    assert store.latest_diagnostic(p.id) is None


async def test_calms_only_saved_and_rerun_and_double_click_do_not_duplicate(gui, store):
    p = store.create_project('CALMS seul')
    store.save_calms(p.id, {})
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.click(f'diagnostic_finalize_{p.id}')
    saved = store.latest_diagnostic(p.id)
    assert len(saved.snapshot.to_dict()['recommendations']) == 5
    await gui.click(f'diagnostic_finalize_{p.id}')
    assert count(store) == 1
    await gui.user.should_see('Résultats du diagnostic sauvegardé')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.user.should_see(saved.id)
    assert store.latest_diagnostic(p.id) == saved


async def test_explicit_new_submission_and_changed_sources_create_distinct_snapshots(gui, store):
    p = store.create_project('Plusieurs')
    store.save_calms(p.id, {})
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.click(f'diagnostic_finalize_{p.id}')
    original = store.latest_diagnostic(p.id)
    await gui.click(f'diagnostic_new_{p.id}')
    await gui.click(f'diagnostic_finalize_{p.id}')
    assert count(store) == 2
    store.save_calms(p.id, {'C1': 3, 'C2': 3})
    await gui.click(f'diagnostic_finalize_{p.id}')
    assert count(store) == 3
    assert store.get_diagnostic(p.id, original.id) == original


async def test_scan_only_then_combined_and_prudent_alignment(gui, store):
    p = store.create_project('Combinaison')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'incomplete_project')
    await gui.set(f'include_scan_{p.id}', True)
    await gui.click(f'diagnostic_finalize_{p.id}')
    scanned = store.latest_diagnostic(p.id).snapshot.to_dict()
    assert scanned['inputs']['calms'] is None
    assert scanned['recommendations'][0]['id'] == 'REC-CMP-001-RISK'
    store.save_calms(p.id, {'A1': 3, 'A2': 3, 'A3': 3})
    await gui.set(f'include_calms_{p.id}', True)
    await gui.click(f'diagnostic_finalize_{p.id}')
    combined = store.latest_diagnostic(p.id).snapshot.to_dict()
    assert len(combined['alignments']) == 1
    assert combined['scores'][1]['score'] == 100
    await gui.user.should_see('Déclaration et indices à rapprocher')


async def test_saved_result_uses_saved_answers_not_unsubmitted_widget_values(gui, store):
    p = store.create_project('Dernière sauvegarde')
    store.save_calms(p.id, {})
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.set(f'calms_{p.id}_C1', 3)
    await gui.click(f'diagnostic_finalize_{p.id}')
    assert store.latest_diagnostic(p.id).snapshot.to_dict()['inputs']['calms']['c1'] == 'unknown'


async def test_skipping_both_available_sources_is_refused(gui, store):
    p = store.create_project('Passer')
    store.save_calms(p.id, {})
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'healthy_project')
    await gui.set(f'include_calms_{p.id}', False)
    await gui.set(f'include_scan_{p.id}', False)
    await gui.click(f'diagnostic_finalize_{p.id}')
    assert not gui.element(f'diagnostic_finalize_{p.id}').enabled
    assert store.latest_diagnostic(p.id) is None


async def test_failed_scan_cannot_finalize_stale_result(gui, store, tmp_path):
    p = store.create_project('Scan rejeté')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'healthy_project')
    await gui.set(f'include_scan_{p.id}', True)
    await gui.set(f'scan_path_{p.id}', str(tmp_path/'FAKE_SECRET_PATH'))
    await gui.click(f'scan_run_{p.id}')
    await gui.click(f'diagnostic_finalize_{p.id}')
    await gui.user.should_see('indisponible')
    assert store.latest_diagnostic(p.id) is None
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)


async def test_filters_do_not_modify_snapshot_and_projects_remain_isolated(gui, store):
    a, b = store.create_project('A'), store.create_project('B')
    store.save_calms(a.id, {'C1': 0, 'C2': 0})
    await gui.open(a.id, 'Diagnostic guidé')
    await gui.click(f'diagnostic_finalize_{a.id}')
    saved = store.latest_diagnostic(a.id)
    await gui.set(f'findings_status_{a.id}', 'risk')
    await gui.user.should_see('1 / 11')
    await gui.set(f'findings_source_{a.id}', 'observé')
    await gui.user.should_see('0 / 11')
    assert store.latest_diagnostic(a.id) == saved
    await gui.nav('Projets')
    await gui.click(f'open_{b.id}')
    await gui.nav('Diagnostic guidé')
    await gui.user.should_not_see(saved.id)
    assert store.latest_diagnostic(b.id) is None


async def test_no_actions_for_favorable_sources_and_no_secret_in_finalized_ui(gui, store):
    p = store.create_project('Favorable')
    store.save_calms(p.id, {q: 3 for q in load_questionnaire().question_ids}, {'C1': 'FAKE_SECRET_NOTE'})
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'healthy_project')
    await gui.set(f'include_scan_{p.id}', True)
    await gui.click(f'diagnostic_finalize_{p.id}')
    assert not store.latest_diagnostic(p.id).snapshot.to_dict()['recommendations']
    await gui.user.should_see('Aucune action automatique')
    # Les notes restent dans le formulaire local ; aucun contenu libre n'apparaît dans les résultats.
    assert 'FAKE_SECRET' not in store.latest_diagnostic(p.id).snapshot.to_json()
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)


async def test_corrupt_saved_version_shows_neutral_error(gui, store):
    p = store.create_project('Corrompu')
    store.save_diagnostic(p.id, 'key', build_diagnostic(answers={}))
    with sqlite3.connect(store.path) as c:
        c.execute('UPDATE diagnostics SET snapshot_json=?', ('{"schema_version":"FAKE_SECRET"}',))
    await gui.open(p.id, 'Diagnostic guidé')
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)
    assert gui.element('data_error')
