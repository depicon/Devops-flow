"""Parcours P4 via GUI simulée ; upload réel et rendu navigateur non couverts."""

from pathlib import Path
import sqlite3

from streamlit.testing.v1 import AppTest

from devops_assist.calms import load_questionnaire
from devops_assist.diagnostic import build_diagnostic

APP = Path(__file__).resolve().parents[1] / "app.py"
FIXTURES = Path(__file__).parent / "fixtures"


def open_diagnostic(project_id):
    at = AppTest.from_file(APP, default_timeout=10).run()
    at.button(key=f"open_{project_id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not at.exception
    return at


def finalize(at, project_id):
    at.button(key=f"diagnostic_finalize_{project_id}").click().run()
    assert not at.exception
    return at


def do_scan(at, project_id, path):
    at.radio(key=f"scan_mode_{project_id}").set_value("Dossier local").run()
    at.text_input(key=f"scan_path_{project_id}").set_value(str(path)).run()
    at.button(key=f"scan_run_{project_id}").click().run()
    at.checkbox(key=f"include_scan_{project_id}").check().run()
    return at


def count_diagnostics(store):
    with sqlite3.connect(store.path) as connection:
        return connection.execute("SELECT count(*) FROM diagnostics").fetchone()[0]


def test_no_sources_refused_and_nothing_persisted(store):
    project = store.create_project("Sans source")
    at = finalize(open_diagnostic(project.id), project.id)
    assert any("questionnaire ou réalisez un scan" in item.value for item in at.error)
    assert store.latest_diagnostic(project.id) is None


def test_calms_only_saved_and_rerun_and_double_click_do_not_duplicate(store):
    project = store.create_project("CALMS seul")
    store.save_calms(project.id, {})
    at = finalize(open_diagnostic(project.id), project.id)
    saved = store.latest_diagnostic(project.id)
    assert saved is not None
    assert len(saved.snapshot.to_dict()["recommendations"]) == 5
    at.run()
    finalize(at, project.id)
    assert count_diagnostics(store) == 1
    assert store.latest_diagnostic(project.id).id == saved.id
    assert any("Dernier diagnostic sauvegardé" in item.value for item in at.subheader)
    reopened = open_diagnostic(project.id)
    assert any(saved.id in item.value for item in reopened.caption)
    assert not reopened.exception


def test_explicit_new_submission_and_changed_sources_create_distinct_snapshots(store):
    project = store.create_project("Plusieurs")
    store.save_calms(project.id, {})
    at = finalize(open_diagnostic(project.id), project.id)
    original = store.latest_diagnostic(project.id)
    at.button(key=f"diagnostic_new_{project.id}").click().run()
    finalize(at, project.id)
    assert count_diagnostics(store) == 2
    store.save_calms(project.id, {"C1": 3, "C2": 3})
    finalize(at, project.id)
    assert count_diagnostics(store) == 3
    assert store.get_diagnostic(project.id, original.id) == original


def test_scan_only_then_combined_and_prudent_alignment(store):
    project = store.create_project("Combinaison")
    at = do_scan(open_diagnostic(project.id), project.id, FIXTURES / "incomplete_project")
    finalize(at, project.id)
    scanned = store.latest_diagnostic(project.id).snapshot.to_dict()
    assert scanned["inputs"]["calms"] is None
    assert scanned["recommendations"][0]["id"] == "REC-CMP-001-RISK"
    store.save_calms(project.id, {"A1": 3, "A2": 3, "A3": 3})
    at.checkbox(key=f"include_calms_{project.id}").check().run()
    finalize(at, project.id)
    combined = store.latest_diagnostic(project.id).snapshot.to_dict()
    assert len(combined["alignments"]) == 1
    assert combined["scores"][1]["score"] == 100
    assert any("Déclaration et indices à rapprocher" in item.value for item in at.info)


def test_saved_result_uses_saved_answers_not_unsubmitted_widget_values(store):
    project = store.create_project("Dernière sauvegarde")
    store.save_calms(project.id, {})
    at = open_diagnostic(project.id)
    at.selectbox(key=f"calms_{project.id}_C1").set_value(3)
    finalize(at, project.id)
    assert store.latest_diagnostic(project.id).snapshot.to_dict()["inputs"]["calms"]["C1"] == "unknown"


def test_skipping_both_available_sources_is_refused(store):
    project = store.create_project("Passer")
    store.save_calms(project.id, {})
    at = do_scan(open_diagnostic(project.id), project.id, FIXTURES / "healthy_project")
    at.checkbox(key=f"include_calms_{project.id}").uncheck()
    at.checkbox(key=f"include_scan_{project.id}").uncheck()
    finalize(at, project.id)
    assert at.error
    assert store.latest_diagnostic(project.id) is None


def test_failed_scan_cannot_finalize_stale_result(store, tmp_path):
    project = store.create_project("Scan rejeté")
    at = do_scan(open_diagnostic(project.id), project.id, FIXTURES / "healthy_project")
    at.text_input(key=f"scan_path_{project.id}").set_value(str(tmp_path / "FAKE_SECRET_PATH")).run()
    at.button(key=f"scan_run_{project.id}").click().run()
    finalize(at, project.id)
    assert any("indisponible" in item.value for item in at.error)
    assert store.latest_diagnostic(project.id) is None
    assert all("FAKE_SECRET" not in item.value for item in at.error)


def test_filters_do_not_modify_snapshot_and_projects_remain_isolated(store):
    first, second = store.create_project("A"), store.create_project("B")
    store.save_calms(first.id, {"C1": 0, "C2": 0})
    at = finalize(open_diagnostic(first.id), first.id)
    saved = store.latest_diagnostic(first.id)
    at.selectbox(key=f"findings_status_{first.id}").set_value("risk").run()
    assert any("1 / 11" in item.value for item in at.caption)
    at.selectbox(key=f"findings_source_{first.id}").set_value("observé").run()
    assert any("0 / 11" in item.value for item in at.caption)
    assert store.latest_diagnostic(first.id) == saved
    at.sidebar.radio[0].set_value("Projets").run()
    at.button(key=f"open_{second.id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not any(saved.id in item.value for item in at.caption)
    assert store.latest_diagnostic(second.id) is None


def test_no_actions_for_favorable_sources_and_no_secret_in_finalized_ui(store, tmp_path):
    project = store.create_project("Favorable")
    store.save_calms(project.id, {q: 3 for q in load_questionnaire().question_ids}, {"C1": "FAKE_SECRET_NOTE"})
    at = do_scan(open_diagnostic(project.id), project.id, FIXTURES / "healthy_project")
    finalize(at, project.id)
    assert not store.latest_diagnostic(project.id).snapshot.to_dict()["recommendations"]
    assert any("Aucune action automatique" in item.value for item in at.info)
    for kind in (at.markdown, at.caption, at.info, at.text):
        assert all("FAKE_SECRET" not in item.value for item in kind)
    assert "FAKE_SECRET" not in store.latest_diagnostic(project.id).snapshot.to_json()


def test_corrupt_saved_version_shows_neutral_error(store):
    project = store.create_project("Corrompu")
    store.save_diagnostic(project.id, "key", build_diagnostic(answers={}))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE diagnostics SET snapshot_json = ?", ('{"schema_version":"FAKE_SECRET"}',))
    at = open_diagnostic(project.id)
    assert at.error
    assert "FAKE_SECRET" not in at.error[0].value
