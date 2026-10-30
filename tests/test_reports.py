"""Formats cohérents, projection sûre, état figé et versions historiques."""

from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from devops_assist.calms import load_questionnaire
from devops_assist.diagnostic import DiagnosticSnapshot, build_diagnostic
from devops_assist.reports import build_export, render_json, render_markdown
from devops_assist.scanner import scan_local, scan_zip, ScanError

FIXTURES = Path(__file__).parent / "fixtures"
DATE = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def make_report(store, *, answers=None, scan=None):
    project = store.create_project("FAKE_SECRET_NAME", "FAKE_SECRET_DESCRIPTION")
    saved = store.save_diagnostic(project.id, str(project.id), build_diagnostic(answers=answers, scan=scan))
    diagnostic, actions = store.diagnostic_state(project.id, saved.id)
    return saved, build_export(diagnostic, actions, exported_at=DATE)


@pytest.mark.parametrize("source", ["calms", "scan", "combined", "favorable"])
def test_same_export_object_feeds_both_formats_with_nulls_accents_and_all_sections(store, source):
    answers = {q: 3 for q in load_questionnaire().question_ids} if source == "favorable" else {}
    saved, report = make_report(store, answers=None if source == "scan" else answers,
                                scan=None if source == "calms" else scan_local(FIXTURES / "healthy_project"))
    data = report.to_dict()
    text, markdown = render_json(report), render_markdown(report)
    assert json.loads(text) == data
    assert data["diagnostic_id"] == saved.id
    assert data["exported_at"] == DATE.isoformat()
    assert DATE.isoformat() in markdown and saved.id in markdown
    for key in ("findings", "scores", "recommendations", "alignments", "versions", "inputs", "limitations"):
        assert data[key] == saved.snapshot.to_dict()[key]
    assert "État des actions" in markdown
    assert "Déclarations" in text and "Déclarations" in markdown
    assert "FAKE_SECRET" not in text + markdown
    assert render_json(report) == text and render_markdown(report) == markdown
    copy = report.to_dict()
    copy["findings"].clear()
    assert report.to_dict()["findings"]
    if source == "calms":
        assert all(s["score"] is None for s in data["scores"])
        assert "null (indéterminé" in markdown
    if source == "favorable":
        assert data["actions"] == data["recommendations"] == []


def test_export_uses_current_order_and_status_but_keeps_original_priorities_and_versions(store):
    saved, original = make_report(store, answers={})
    before, actions = store.diagnostic_state(saved.project_id, saved.id)
    store.update_action(saved.project_id, saved.id, actions[-1].id, status="dismissed", notes="FAKE_SECRET_COMMENT")
    store.move_action(saved.project_id, saved.id, actions[-1].id, 0)
    after, updated = store.diagnostic_state(saved.project_id, saved.id)
    report = build_export(after, updated, exported_at=DATE)
    data = report.to_dict()
    assert after == before
    assert data["actions"][0]["id"] == actions[-1].id
    assert data["actions"][0]["status"] == "dismissed"
    assert data["actions"][0]["created_at"] == actions[-1].created_at.isoformat()
    assert data["actions"][0]["updated_at"] == updated[0].updated_at.isoformat()
    assert data["recommendations"] == original.to_dict()["recommendations"]
    assert original.to_dict()["actions"][0]["id"] == actions[0].id
    md = render_markdown(report)
    assert md.index(actions[-1].id) < md.index(actions[0].id)
    assert "Écartée" in md
    assert "FAKE_SECRET" not in render_json(report) + md
    for action in data["actions"]:
        assert set(action) == {"id", "recommendation_id", "position", "status", "created_at", "updated_at"}


def test_secrets_from_zip_configs_archive_names_jobs_services_paths_notes_and_exceptions_never_exported(store, tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Dockerfile", "FROM example/FAKE_SECRET_IMAGE:latest\nUSER root\n# FAKE_SECRET_RAW\n")
        archive.writestr("compose.yaml", "services:\n  FAKE_SECRET_SERVICE:\n    privileged: true\n    environment:\n      PASSWORD: FAKE_SECRET_ENV\n")
        archive.writestr(".gitlab-ci.yml", "FAKE_SECRET_JOB:\n  stage: test\n  script: echo FAKE_SECRET_SCRIPT\n")
        archive.writestr(".env", "PASSWORD=FAKE_SECRET_ENV_FILE")
    project = store.create_project("FAKE_SECRET_NAME", "FAKE_SECRET_CONTEXT")
    store.save_calms(project.id, {}, {"C1": "FAKE_SECRET_NOTE"})
    scan = scan_zip(buffer.getvalue())
    saved = store.save_diagnostic(project.id, "secret", build_diagnostic(answers={}, scan=scan))
    diagnostic, actions = store.diagnostic_state(project.id, saved.id)
    store.update_action(project.id, saved.id, actions[0].id, status="in_progress", notes="FAKE_SECRET_COMMENT")
    diagnostic, actions = store.diagnostic_state(project.id, saved.id)
    # Même les colonnes de présentation du suivi ne sont pas une source exportable.
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE actions SET title = ?, description = ?", ("FAKE_SECRET_TITLE", "FAKE_SECRET_DESCRIPTION"))
    report = build_export(diagnostic, actions, exported_at=DATE)
    combined = render_json(report) + render_markdown(report)
    assert "FAKE_SECRET" not in combined
    assert str(tmp_path) not in combined
    assert "FROM example" not in combined and "PASSWORD=" not in combined
    with pytest.raises(ScanError) as error:
        scan_local(tmp_path / "FAKE_SECRET_PATH")
    assert "FAKE_SECRET" not in str(error.value)
    assert store.get_diagnostic(project.id, saved.id) == diagnostic


def test_extra_context_fields_are_projected_out_without_loading_current_catalogs(store, monkeypatch):
    saved, _ = make_report(store, answers={})
    data = saved.snapshot.to_dict()
    data["notes"] = "FAKE_SECRET_EXTRA"
    data["inputs"]["calms"]["notes"] = "FAKE_SECRET_NESTED"
    data["versions"]["recommendations"] = "recommendations-historical"
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE diagnostics SET snapshot_json = ? WHERE id = ?", (json.dumps(data), saved.id))
    def forbidden(*args, **kwargs):
        pytest.fail("Recalcul ou catalogue courant appelé")
    monkeypatch.setattr("devops_assist.diagnostic.build_diagnostic", forbidden)
    monkeypatch.setattr("devops_assist.diagnostic.calculate_scores", forbidden)
    monkeypatch.setattr("devops_assist.diagnostic.load_catalog", forbidden)
    monkeypatch.setattr("devops_assist.diagnostic.load_questionnaire", forbidden)
    diagnostic, actions = store.diagnostic_state(saved.project_id, saved.id)
    report = build_export(diagnostic, actions, exported_at=DATE)
    assert report.to_dict()["versions"]["recommendations"] == "recommendations-historical"
    assert "FAKE_SECRET" not in render_json(report) + render_markdown(report)


@pytest.mark.parametrize("corruption", ["missing_action", "wrong_project", "wrong_diagnostic", "position", "status", "source", "file", "answer", "question", "evidence_file"])
def test_incompatible_data_refused_neutrally(store, corruption):
    saved, _ = make_report(store, answers={}, scan=scan_local(FIXTURES / "incomplete_project"))
    diagnostic, actions = store.diagnostic_state(saved.project_id, saved.id)
    data = diagnostic.snapshot.to_dict()
    if corruption == "missing_action":
        actions = actions[:-1]
    elif corruption in {"wrong_project", "wrong_diagnostic", "position", "status"}:
        field = {"wrong_project": "project_id", "wrong_diagnostic": "diagnostic_id", "position": "position", "status": "status"}[corruption]
        actions = (replace(actions[0], **{field: 99 if field == "position" else "FAKE_SECRET"}), *actions[1:])
    else:
        if corruption == "source": data["inputs"]["scan"]["source"] = "FAKE_SECRET"
        if corruption == "file": data["inputs"]["scan"]["files"] = ["/FAKE_SECRET/config"]
        if corruption == "answer": data["inputs"]["calms"]["C1"] = "FAKE_SECRET"
        if corruption == "question": data["findings"][0]["evidence"][0]["question_id"] = "FAKE_SECRET"
        if corruption == "evidence_file": data["findings"][0]["evidence"][0]["file"] = "/FAKE_SECRET/config"
        diagnostic = replace(diagnostic, snapshot=DiagnosticSnapshot(json.dumps(data)))
    with pytest.raises(ValueError, match="rapport") as error:
        build_export(diagnostic, actions, exported_at=DATE)
    assert "FAKE_SECRET" not in str(error.value)
