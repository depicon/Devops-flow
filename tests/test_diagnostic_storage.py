from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from devops_assist.db import ProjectStore
from devops_assist.diagnostic import build_diagnostic


def counts(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("diagnostics", "actions"))


def test_migration_idempotent_draft_preserved_and_actions_materialized(store):
    project = store.create_project("Projet", "FAKE_SECRET_CONTEXT")
    draft = store.save_calms(project.id, {"C1": 0}, {"C1": "FAKE_SECRET_NOTE"})
    store.initialize()
    store.initialize()
    snapshot = build_diagnostic(answers=draft.answers)
    saved = store.save_diagnostic(project.id, "submission", snapshot)
    assert store.load_calms(project.id) == draft
    assert counts(store.path) == (1, 5)
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute("SELECT recommendation_id, position, status, notes, created_at, updated_at FROM actions ORDER BY position").fetchall()
    for position, (row, action) in enumerate(zip(rows, snapshot.to_dict()["recommendations"])):
        assert row[:4] == (action["id"], position, "todo", "")
        assert row[4] == row[5] == saved.created_at.isoformat()
    assert "FAKE_SECRET" not in saved.snapshot.to_json()
    assert ProjectStore(store.path).get_diagnostic(project.id, saved.id) == saved


def test_repeated_and_concurrent_submission_is_idempotent(store):
    project = store.create_project("Idempotent")
    snapshot = build_diagnostic(answers={})
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: ProjectStore(store.path).save_diagnostic(project.id, "same", snapshot), range(4)))
    assert len({r.id for r in results}) == 1
    assert counts(store.path) == (1, 5)
    # Une relivraison garde le snapshot original même si le brouillon a évolué.
    changed = build_diagnostic(answers={"C1": 3})
    assert store.save_diagnostic(project.id, "same", changed) == results[0]


def test_new_submission_preserves_previous_snapshot_and_isolates_projects(store, monkeypatch):
    project, other = store.create_project("A"), store.create_project("B")
    original = store.save_diagnostic(project.id, "one", build_diagnostic(answers={}))
    second = store.save_diagnostic(project.id, "two", build_diagnostic(answers={"C1": 3, "C2": 3}))
    assert second.id != original.id
    assert store.latest_diagnostic(project.id).id == second.id
    assert store.latest_diagnostic(other.id) is None
    assert store.get_diagnostic(other.id, original.id) is None
    with pytest.raises(ValueError, match="autre projet"):
        store.save_diagnostic(other.id, "one", build_diagnostic(answers={}))
    import devops_assist.diagnostic as diagnostic
    def forbidden(*args, **kwargs):
        raise AssertionError("Recalcul interdit à la lecture")
    monkeypatch.setattr(diagnostic, "calculate_scores", forbidden)
    monkeypatch.setattr(diagnostic, "recommend", forbidden)
    monkeypatch.setattr(diagnostic, "load_questionnaire", forbidden)
    assert ProjectStore(store.path).get_diagnostic(project.id, original.id) == original


def test_failure_during_action_creation_rolls_back_whole_diagnostic(store):
    project = store.create_project("Transaction")
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER fail_action BEFORE INSERT ON actions WHEN NEW.position = 1
                              BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_diagnostic(project.id, "retry", build_diagnostic(answers={}))
    assert counts(store.path) == (0, 0)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER fail_action")
    store.save_diagnostic(project.id, "retry", build_diagnostic(answers={}))
    assert counts(store.path) == (1, 5)


def test_foreign_keys_enforce_action_project_and_diagnostic_match(store):
    project, other = store.create_project("A"), store.create_project("B")
    stored = store.save_diagnostic(project.id, "one", build_diagnostic(answers={}))
    with store._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE actions SET project_id = ? WHERE diagnostic_id = ?", (other.id, stored.id))
    assert counts(store.path) == (1, 5)
    with pytest.raises(ValueError, match="projet"):
        store.save_diagnostic("missing", "two", build_diagnostic(answers={}))


def test_snapshot_keeps_old_versions_and_unknown_schema_errors_neutrally(store):
    project = store.create_project("Versions")
    original = store.save_diagnostic(project.id, "one", build_diagnostic(answers={}))
    data = original.snapshot.to_dict()
    data["versions"]["recommendations"] = "recommendations-old"
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE diagnostics SET snapshot_json = ?", (json.dumps(data),))
    assert store.get_diagnostic(project.id, original.id).snapshot.to_dict()["versions"]["recommendations"] == "recommendations-old"
    data["schema_version"] = "future-FAKE_SECRET"
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE diagnostics SET snapshot_json = ?", (json.dumps(data),))
    with pytest.raises(ValueError, match="illisible") as error:
        store.latest_diagnostic(project.id)
    assert "FAKE_SECRET" not in str(error.value)
