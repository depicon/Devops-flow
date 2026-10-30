from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from devops_assist.db import ProjectStore

APP = Path(__file__).resolve().parents[1] / "app.py"


def app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=10).run()


@pytest.mark.parametrize(
    ("page", "heading"),
    [("Projets", "Accueil / Projets"), ("Diagnostic guidé", "Diagnostic guidé"),
     ("Plan d'action", "Plan d'action"), ("Historique / Rapports", "Historique / Rapports")],
)
def test_each_page_is_accessible(page, heading):
    at = app()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception
    assert at.header[0].value == heading
    if page != "Projets":
        assert at.warning


def test_create_open_and_reopen_project_in_new_ui_session(db_path):
    at = app()
    at.text_input(key="project_name").set_value("Projet Démonstration")
    at.text_area(key="project_description").set_value("Contexte conservé après relance")
    at.button[0].click().run()
    assert not at.exception
    assert at.success
    project = ProjectStore(db_path).list_projects()[0]
    assert at.session_state["active_project_id"] == project.id

    # Nouvelle session sans état Streamlit : la fiche est relue depuis SQLite.
    reopened = app()
    assert any(t.value == project.name for t in reopened.text)
    reopened.button(key=f"open_{project.id}").click().run()
    assert not reopened.exception
    assert reopened.session_state["active_project_id"] == project.id
    assert any(t.value == project.description for t in reopened.text)
    reopened.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert any(t.value == project.name for t in reopened.text)


def test_blank_name_shows_french_error_and_creates_nothing(db_path):
    at = app()
    at.text_input(key="project_name").set_value("   ")
    at.button[0].click().run()
    assert not at.exception
    assert at.error[0].value == "Le nom du projet est obligatoire."
    assert ProjectStore(db_path).list_projects() == []


def test_selecting_a_different_project_replaces_the_displayed_context(store):
    first = store.create_project("Projet A", "Contexte A")
    second = store.create_project("Projet B", "Contexte B")
    at = app()
    at.button(key=f"open_{first.id}").click().run()
    assert any(t.value == "Contexte A" for t in at.text)
    at.button(key=f"open_{second.id}").click().run()
    assert any(t.value == "Contexte B" for t in at.text)
    assert not any(t.value == "Contexte A" for t in at.text)


def test_storage_error_is_neutral(db_path):
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"invalid-db-with-FAKE_SECRET_P1")
    at = app()
    assert not at.exception
    assert "stockage local est inaccessible" in at.error[0].value
    assert "FAKE_SECRET_P1" not in at.error[0].value
    assert str(db_path) not in at.error[0].value
