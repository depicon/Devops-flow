"""Démo installable, résultats reproductibles et isolation réelle du stockage."""

from io import BytesIO
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest

from devops_flow.db import ProjectStore
from devops_flow.demo import DEMO_FILES, DEMO_LIMITATION, load_demo, repository_zip
from devops_flow.diagnostic import build_diagnostic
from devops_flow.reports import build_export, render_json, render_markdown
from devops_flow.scanner import scan_zip

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_bundled_archive_is_deterministic_admissible_and_only_targeted_files():
    payload = repository_zip()
    assert payload == repository_zip()
    with ZipFile(BytesIO(payload)) as archive:
        assert tuple(archive.namelist()) == DEMO_FILES
    scan = scan_zip(payload)
    assert {f.rule_id: f.status for f in scan.findings} == {
        "DOC-001": "observed", "DOC-002": "risk", "DOC-003": "risk",
        "CMP-001": "risk", "CI-001": "observed", "CI-002": "unknown",
    }


def test_demo_expected_scores_coverage_priorities_and_prudent_alignment(store):
    project, diagnostic = load_demo(store)
    assert "fictive" in project.name and "fictif" in project.description
    data = diagnostic.snapshot.to_dict()
    assert DEMO_LIMITATION in data["limitations"]
    assert [s["score"] for s in data["scores"]] == pytest.approx([200 / 9, 100, 100 / 3, 50, None])
    assert [s["answered"] for s in data["scores"]] == [3, 3, 3, 2, 0]
    assert [r["id"] for r in data["recommendations"]] == [
        "REC-CMP-001-RISK", "REC-DOC-002-RISK", "REC-DOC-003-RISK",
        "REC-CAL-C-RISK", "REC-CAL-L-RISK", "REC-CAL-S-VERIFY", "REC-CI-002-VERIFY",
    ]
    assert data["alignments"][0]["id"] == "ALIGN-a2-CI-002"
    assert "Aucune contradiction démontrée" in data["alignments"][0]["limitation"]
    assert data["inputs"]["calms"] == store.load_calms(project.id, profile=store.project_profile(project.id)).answers


def test_each_load_is_new_reproducible_and_never_overwrites_existing_followup(store):
    real = store.create_project("Projet personnel", "Contexte conservé")
    store.save_calms(real.id, {"C1": 3}, {"C1": "Note locale"})
    original = store.save_diagnostic(real.id, "personal", build_diagnostic(answers={"C1": 3}))
    real = store.get_project(real.id)
    first, first_diagnostic = load_demo(store)
    action = store.diagnostic_state(first.id, first_diagnostic.id)[1][0]
    store.update_action(first.id, first_diagnostic.id, action.id, status="done", notes="Préserver")
    before = store.diagnostic_state(first.id, first_diagnostic.id)
    second, second_diagnostic = load_demo(store)
    assert len({real.id, first.id, second.id}) == 3
    assert first_diagnostic.id != second_diagnostic.id
    assert first_diagnostic.snapshot == second_diagnostic.snapshot
    assert store.diagnostic_state(first.id, first_diagnostic.id) == before
    assert store.get_project(real.id) == real
    assert store.load_calms(real.id).notes == {"C1": "Note locale"}
    assert store.latest_diagnostic(real.id) == original


def test_atomic_rollback_leaves_no_partial_demo_if_action_insert_fails(store):
    real = store.create_project("À préserver")
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER fail_demo BEFORE INSERT ON actions
                            WHEN NEW.position = 1 BEGIN SELECT RAISE(ABORT, 'test'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        load_demo(store)
    assert store.list_projects() == [real]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM diagnostics").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


def test_scanner_failure_creates_nothing(store, monkeypatch):
    monkeypatch.setattr("devops_flow.demo.repository_zip", lambda: b"invalid ZIP")
    with pytest.raises(ValueError):
        load_demo(store)
    assert store.list_projects() == []


def test_demo_reopens_without_engine_and_fiction_label_survives_reports(store, monkeypatch):
    project, diagnostic = load_demo(store)
    def forbidden(*args, **kwargs):
        pytest.fail("Le moteur ne doit pas être rappelé à la lecture")
    monkeypatch.setattr("devops_flow.demo.build_diagnostic", forbidden)
    monkeypatch.setattr("devops_flow.demo.scan_zip", forbidden)
    reopened = ProjectStore(store.path)
    stored, actions = reopened.diagnostic_state(project.id, diagnostic.id)
    report = build_export(stored, actions)
    assert DEMO_LIMITATION in render_markdown(report)
    assert DEMO_LIMITATION in render_json(report)
    assert stored.snapshot == diagnostic.snapshot


async def test_ui_demo_selects_new_project_and_reopens_all_spaces_without_overwrite(gui, store):
    real = store.create_project("Projet existant")
    await gui.open(real.id)
    await gui.click('load_demo')
    project_id = gui.session.active_project_id
    assert project_id != real.id
    assert len(store.list_projects()) == 2
    diagnostic = store.latest_diagnostic(project_id)
    for page in ('Diagnostic guidé', "Plan d'action", 'Historique / Rapports'):
        await gui.nav(page)
        await gui.user.should_see('fictive')
        if page == 'Diagnostic guidé':
            await gui.user.should_see('11/15 réponses connues (73,3 %)')
            await gui.user.should_see('3/3 réponses connues (100,0 %)')
    await gui.nav('Projets')
    await gui.click('load_demo')
    assert len(store.list_projects()) == 3
    assert store.latest_diagnostic(project_id) == diagnostic
    assert store.get_project(real.id) == real


async def test_ui_demo_failure_is_neutral_and_does_not_select_partial_project(gui, store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER fail_demo BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT, 'FAKE_SECRET'); END")
    await gui.open()
    await gui.click('load_demo')
    await gui.user.should_see('stockage local')
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)
    assert store.list_projects() == []
    assert gui.session.active_project_id is None
