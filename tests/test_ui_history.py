"""Parcours P5 AppTest : widgets et données fournies aux boutons, sans transport navigateur."""

import json
from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from devops_assist.calms import load_questionnaire
from devops_assist.diagnostic import build_diagnostic
from devops_assist.reports import render_json, render_markdown

APP = Path(__file__).resolve().parents[1] / "app.py"


def open_page(project_id, page):
    at = AppTest.from_file(APP, default_timeout=10).run()
    at.button(key=f"open_{project_id}").click().run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception
    return at


@pytest.fixture
def saved(store):
    project = store.create_project("Projet A")
    return store.save_diagnostic(project.id, "one", build_diagnostic(answers={}))


@pytest.fixture
def downloads(monkeypatch):
    import streamlit as st
    original = st.download_button
    captured = {}
    def capture(label, data, **kwargs):
        captured[kwargs["key"]] = {"data": data, **kwargs}
        return original(label, data, **kwargs)
    monkeypatch.setattr(st, "download_button", capture)
    return captured


@pytest.mark.parametrize("page", ["Plan d'action", "Historique / Rapports"])
def test_project_without_diagnostic_has_guidance(store, page):
    project = store.create_project("Vide")
    at = open_page(project.id, page)
    assert any("Aucun diagnostic enregistré" in item.value for item in at.info)


def test_ui_saves_all_statuses_comment_and_order_then_reopens(store, saved):
    before, actions = store.diagnostic_state(saved.project_id, saved.id)
    action = actions[1]
    at = open_page(saved.project_id, "Plan d'action")
    for status in ("in_progress", "done", "dismissed", "todo"):
        at.selectbox(key=f"action_status_{action.id}").set_value(status)
        at.text_area(key=f"action_notes_{action.id}").set_value("Commentaire local **littéral** é 🧭")
        at.button(key=f"action_save_{action.id}").click().run()
        assert not at.exception
        updated = store.diagnostic_state(saved.project_id, saved.id)[1][1]
        assert updated.status == status
        assert updated.notes == "Commentaire local **littéral** é 🧭"
    at.button(key=f"action_up_{action.id}").click().run()
    assert store.diagnostic_state(saved.project_id, saved.id)[1][0].id == action.id
    at = open_page(saved.project_id, "Plan d'action")
    assert at.text_area(key=f"action_notes_{action.id}").value == updated.notes
    assert at.button(key=f"action_up_{action.id}").disabled
    assert at.button(key=f"action_down_{actions[-1].id}").disabled
    at.button(key=f"action_down_{action.id}").click().run()
    assert not at.exception
    assert store.diagnostic_state(saved.project_id, saved.id)[1][1].id == action.id
    assert store.get_diagnostic(saved.project_id, saved.id) == before


def test_old_diagnostic_selection_without_engine_and_independent_followup(store, saved, monkeypatch):
    second = store.save_diagnostic(saved.project_id, "two", build_diagnostic(answers={"C1": 3, "C2": 3}))
    def forbidden(*args, **kwargs):
        pytest.fail("Historique : moteur ou scanner appelé")
    monkeypatch.setattr("devops_assist.diagnostic.build_diagnostic", forbidden)
    monkeypatch.setattr("devops_assist.diagnostic.calculate_scores", forbidden)
    monkeypatch.setattr("devops_assist.scanner.scan_local", forbidden)
    monkeypatch.setattr("devops_assist.scanner.scan_zip", forbidden)
    at = open_page(saved.project_id, "Historique / Rapports")
    selector = f"history_diagnostic_{saved.project_id}"
    assert at.selectbox(key=selector).value == second.id
    at.selectbox(key=selector).set_value(saved.id).run()
    assert not at.exception
    assert any(saved.id in item.value for item in at.caption)
    assert at.session_state[f"report_{saved.project_id}_{saved.id}"][1].to_dict()["diagnostic_id"] == saved.id
    at.sidebar.radio[0].set_value("Plan d'action").run()
    at.selectbox(key=f"roadmap_diagnostic_{saved.project_id}").set_value(saved.id).run()
    action = store.diagnostic_state(saved.project_id, saved.id)[1][0]
    at.selectbox(key=f"action_status_{action.id}").set_value("done")
    at.button(key=f"action_save_{action.id}").click().run()
    assert all(a.status == "todo" for a in store.diagnostic_state(saved.project_id, second.id)[1])
    assert store.get_diagnostic(saved.project_id, saved.id) == saved


def test_report_buttons_previews_share_bytes_timestamp_and_refresh_after_followup(store, saved, downloads):
    at = open_page(saved.project_id, "Historique / Rapports")
    key = f"report_{saved.project_id}_{saved.id}"
    original = at.session_state[key][1]
    md_key, json_key = f"download_md_{saved.id}", f"download_json_{saved.id}"
    assert downloads[md_key]["data"] == render_markdown(original).encode("utf-8")
    assert downloads[json_key]["data"] == render_json(original).encode("utf-8")
    assert saved.project_id in downloads[json_key]["file_name"]
    assert downloads[md_key]["mime"].startswith("text/markdown")
    assert downloads[json_key]["mime"] == "application/json"
    assert at.code[0].value == render_json(original).strip()
    assert any(item.value == render_markdown(original).strip() for item in at.markdown)
    at.run()
    assert at.session_state[key][1] == original
    action = store.diagnostic_state(saved.project_id, saved.id)[1][0]
    store.update_action(saved.project_id, saved.id, action.id, status="done", notes="FAKE_SECRET_COMMENT")
    at.run()
    refreshed = at.session_state[key][1]
    assert refreshed != original
    assert refreshed.to_dict()["actions"][0]["status"] == "done"
    assert json.loads(downloads[json_key]["data"])["exported_at"] == refreshed.to_dict()["exported_at"]
    assert refreshed.to_dict()["exported_at"] in downloads[md_key]["data"].decode("utf-8")
    assert "FAKE_SECRET" not in downloads[json_key]["data"].decode("utf-8")
    at.button(key=f"report_refresh_{saved.id}").click().run()
    assert at.session_state[key][1].to_dict()["exported_at"] != refreshed.to_dict()["exported_at"]
    assert not at.exception


@pytest.mark.parametrize("page", ["Plan d'action", "Historique / Rapports"])
def test_project_switch_does_not_leak_other_diagnostics(store, saved, page):
    other = store.create_project("Projet B")
    at = open_page(saved.project_id, page)
    at.sidebar.radio[0].set_value("Projets").run()
    at.button(key=f"open_{other.id}").click().run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception
    assert not any(saved.id in item.value for item in at.caption)
    assert any("Aucun diagnostic enregistré" in item.value for item in at.info)


@pytest.mark.parametrize("page", ["Plan d'action", "Historique / Rapports"])
def test_favorable_diagnostic_has_no_editable_actions(store, page):
    project = store.create_project("Favorable")
    store.save_diagnostic(project.id, "favorable", build_diagnostic(answers={q: 3 for q in load_questionnaire().question_ids}))
    at = open_page(project.id, page)
    assert not at.exception
    assert not at.text_area
    assert any("Aucune action" in item.value for item in at.info)


@pytest.mark.parametrize("page", ["Plan d'action", "Historique / Rapports"])
def test_corrupt_history_shows_neutral_error_and_other_diagnostic_still_opens(store, saved, page):
    second = store.save_diagnostic(saved.project_id, "two", build_diagnostic(answers={}))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE diagnostics SET snapshot_json = ? WHERE id = ?", ('FAKE_SECRET_CORRUPT', second.id))
    at = open_page(saved.project_id, page)
    assert at.error
    assert all("FAKE_SECRET" not in item.value for item in at.error)
    space = "history" if page == "Historique / Rapports" else "roadmap"
    at.selectbox(key=f"{space}_diagnostic_{saved.project_id}").set_value(saved.id).run()
    assert not at.exception and not at.error


def test_minimal_guided_journey_to_followup_and_both_report_payloads(store, downloads):
    at = AppTest.from_file(APP, default_timeout=10).run()
    at.text_input(key="project_name").set_value("Parcours complet P5")
    at.button[0].click().run()
    project_id = at.session_state["active_project_id"]
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    # Le questionnaire entièrement inconnu reste une entrée admissible.
    next(b for b in at.button if b.label == "Enregistrer et calculer").click().run()
    at.checkbox(key=f"include_calms_{project_id}").check().run()
    at.button(key=f"diagnostic_finalize_{project_id}").click().run()
    saved = store.latest_diagnostic(project_id)
    at.sidebar.radio[0].set_value("Plan d'action").run()
    action = store.diagnostic_state(project_id, saved.id)[1][0]
    at.selectbox(key=f"action_status_{action.id}").set_value("in_progress")
    at.button(key=f"action_save_{action.id}").click().run()
    at.sidebar.radio[0].set_value("Historique / Rapports").run()
    assert not at.exception
    assert json.loads(downloads[f"download_json_{saved.id}"]["data"])["actions"][0]["status"] == "in_progress"
    assert "En cours" in downloads[f"download_md_{saved.id}"]["data"].decode("utf-8")
