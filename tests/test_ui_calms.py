import json
from pathlib import Path
import sqlite3

from streamlit.testing.v1 import AppTest

from devops_assist.db import ProjectStore

APP = Path(__file__).resolve().parents[1] / "app.py"


def open_questionnaire(project_id):
    at = AppTest.from_file(APP, default_timeout=10).run()
    at.button(key=f"open_{project_id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not at.exception
    return at


def test_no_questionnaire_until_a_project_is_selected():
    at = AppTest.from_file(APP).run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not at.exception
    assert not at.selectbox
    assert any("Créez ou ouvrez" in item.value for item in at.info)


def test_unknown_save_shows_five_indeterminate_scores(store):
    project = store.create_project("Sans information")
    at = open_questionnaire(project.id)
    assert len(at.selectbox) == 15
    assert not at.metric
    at.button[0].click().run()
    assert not at.exception
    assert at.success
    assert len(at.metric) == 5
    assert all(metric.value == "Indéterminé" for metric in at.metric)
    assert any("0/15 réponses connues (0,0 %)" in item.value for item in at.markdown)


def test_answers_notes_and_scores_survive_new_ui_session(store, db_path):
    project = store.create_project("Questionnaire conservé")
    at = open_questionnaire(project.id)
    at.selectbox(key=f"calms_{project.id}_C1").set_value(3)
    at.selectbox(key=f"calms_{project.id}_C2").set_value(0)
    at.selectbox(key=f"calms_{project.id}_A1").set_value(1)
    at.text_area(key=f"calms_{project.id}_C1_note").set_value("Précision d'équipe")
    at.button[0].click().run()
    assert not at.exception
    assert at.metric[0].value == "50,0 / 100"
    assert at.metric[1].value == "33,3 / 100"
    assert any("2/3 réponses connues · Couverture : 66,7 %" in item.value for item in at.caption)
    assert any("3/15 réponses connues (20,0 %)" in item.value for item in at.markdown)

    reopened = open_questionnaire(project.id)
    assert reopened.selectbox(key=f"calms_{project.id}_C1").value == 3
    assert reopened.selectbox(key=f"calms_{project.id}_C2").value == 0
    assert reopened.text_area(key=f"calms_{project.id}_C1_note").value == "Précision d'équipe"
    assert reopened.metric[0].value == "50,0 / 100"
    assert ProjectStore(db_path).load_calms(project.id).answers["C1"] == 3


def test_unsaved_edits_do_not_change_saved_scores_or_database(store):
    project = store.create_project("Sauvegarde explicite")
    original = store.save_calms(project.id, {"C1": 3})
    at = open_questionnaire(project.id)
    # Dans un formulaire, une modification reste côté client jusqu'au clic.
    # AppTest.run() seul recharge les valeurs serveur non soumises.
    at.selectbox(key=f"calms_{project.id}_C1").set_value(0)
    assert at.metric[0].value == "100,0 / 100"
    assert store.load_calms(project.id) == original
    at.button[0].click().run()
    assert at.metric[0].value == "0,0 / 100"
    assert store.load_calms(project.id).answers["C1"] == 0
    at.selectbox(key=f"calms_{project.id}_C1").set_value(3)
    at.button[0].click().run()
    assert not at.exception
    assert at.metric[0].value == "100,0 / 100"
    assert store.load_calms(project.id).answers["C1"] == 3


def test_questionnaires_do_not_leak_between_projects_or_navigation(store):
    first = store.create_project("A")
    second = store.create_project("B")
    store.save_calms(first.id, {"C1": 3}, {"C1": "Note A"})
    store.save_calms(second.id, {"C1": 0}, {"C1": "Note B"})
    at = open_questionnaire(first.id)
    at.sidebar.radio[0].set_value("Projets").run()
    at.button(key=f"open_{second.id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not at.exception
    assert at.selectbox(key=f"calms_{second.id}_C1").value == 0
    assert at.text_area(key=f"calms_{second.id}_C1_note").value == "Note B"
    at.sidebar.radio[0].set_value("Projets").run()
    at.button(key=f"open_{first.id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert at.selectbox(key=f"calms_{first.id}_C1").value == 3
    assert at.text_area(key=f"calms_{first.id}_C1_note").value == "Note A"


def test_unsupported_saved_version_is_not_overwritten_or_disclosed(store, db_path):
    project = store.create_project("Version inconnue")
    raw = json.dumps({"version": "future", "notes": "FAKE_SECRET_CALMS"})
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE projects SET draft_answers_json = ? WHERE id = ?", (raw, project.id))
    at = open_questionnaire(project.id)
    assert not at.exception
    assert at.error
    assert "FAKE_SECRET_CALMS" not in at.error[0].value
    assert not at.selectbox
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT draft_answers_json FROM projects WHERE id = ?", (project.id,)).fetchone()[0] == raw
