"""Suivi persistant, transactions et isolation des actions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from devops_flow.db import ProjectStore
from devops_flow.diagnostic import build_diagnostic
from devops_flow.roadmap import STATUS_LABELS


@pytest.fixture
def saved(store):
    project = store.create_project("Projet")
    return store.save_diagnostic(project.id, "one", build_diagnostic(answers={}))


@pytest.mark.parametrize("status", STATUS_LABELS)
def test_followup_survives_reopening_and_keeps_snapshot_and_creation_date(store, saved, status):
    before, actions = store.diagnostic_state(saved.project_id, saved.id)
    action = actions[0]
    store.update_action(saved.project_id, saved.id, action.id, status=status, notes="Équipe : à confirmer 🧭")
    reopened = ProjectStore(store.path)
    after, updated = reopened.diagnostic_state(saved.project_id, saved.id)
    assert after == before
    assert updated[0].status == status
    assert updated[0].notes == "Équipe : à confirmer 🧭"
    assert updated[0].created_at == action.created_at
    assert updated[0].updated_at >= action.updated_at
    assert updated[1:] == actions[1:]
    store.update_action(saved.project_id, saved.id, action.id, status=status, notes=updated[0].notes)
    assert reopened.diagnostic_state(saved.project_id, saved.id)[1] == updated  # no-op dates stable


@pytest.mark.parametrize("status", ["finished", "", None, True, [], "done' WHERE 1=1 --"])
def test_invalid_status_refused_without_changes(store, saved, status):
    before = store.diagnostic_state(saved.project_id, saved.id)
    with pytest.raises(ValueError, match="statut"):
        store.update_action(saved.project_id, saved.id, before[1][0].id, status=status, notes="")
    assert store.diagnostic_state(saved.project_id, saved.id) == before


@pytest.mark.parametrize("notes", [None, 1, "x" * 2001])
def test_invalid_notes_refused(store, saved, notes):
    before = store.diagnostic_state(saved.project_id, saved.id)
    with pytest.raises(ValueError, match="commentaire"):
        store.update_action(saved.project_id, saved.id, before[1][0].id, status="done", notes=notes)
    assert store.diagnostic_state(saved.project_id, saved.id) == before


def test_comment_boundary_and_parameterized_queries(store, saved):
    action = store.diagnostic_state(saved.project_id, saved.id)[1][0]
    for notes in ("é" * 2000, "'); DROP TABLE actions; --"):
        store.update_action(saved.project_id, saved.id, action.id, status="done", notes=notes)
        assert store.diagnostic_state(saved.project_id, saved.id)[1][0].notes == notes


def test_move_reorders_contiguously_and_keeps_scores_and_other_fields(store, saved):
    diagnostic, actions = store.diagnostic_state(saved.project_id, saved.id)
    store.move_action(saved.project_id, saved.id, actions[-1].id, 0)
    after, moved = ProjectStore(store.path).diagnostic_state(saved.project_id, saved.id)
    assert after == diagnostic
    assert [a.id for a in moved] == [actions[-1].id, *[a.id for a in actions[:-1]]]
    assert [a.position for a in moved] == list(range(len(actions)))
    for action in moved:
        old = next(a for a in actions if a.id == action.id)
        assert replace(action, position=old.position, updated_at=old.updated_at) == old
    store.move_action(saved.project_id, saved.id, actions[-1].id, len(actions) - 1)
    assert [a.id for a in store.diagnostic_state(saved.project_id, saved.id)[1]] == [a.id for a in actions]
    before = store.diagnostic_state(saved.project_id, saved.id)
    store.move_action(saved.project_id, saved.id, actions[0].id, 0)
    assert store.diagnostic_state(saved.project_id, saved.id) == before


@pytest.mark.parametrize("position", [-1, 5, True, 1.0, "1", None])
def test_invalid_order_refused(store, saved, position):
    before = store.diagnostic_state(saved.project_id, saved.id)
    with pytest.raises(ValueError, match="position"):
        store.move_action(saved.project_id, saved.id, before[1][0].id, position)
    assert store.diagnostic_state(saved.project_id, saved.id) == before


def test_actions_and_history_are_isolated_between_projects_and_diagnostics(store, saved):
    other = store.create_project("Autre")
    second = store.save_diagnostic(saved.project_id, "two", build_diagnostic(answers={"C1": 0}))
    action = store.diagnostic_state(saved.project_id, saved.id)[1][0]
    for project_id, diagnostic_id in ((other.id, saved.id), (saved.project_id, second.id), ("missing", "missing")):
        for change in (lambda: store.update_action(project_id, diagnostic_id, action.id, status="done", notes=""),
                       lambda: store.move_action(project_id, diagnostic_id, action.id, 0)):
            with pytest.raises(ValueError, match="disponible"):
                change()
    with pytest.raises(ValueError, match="disponible"):
        store.diagnostic_state(other.id, saved.id)
    assert [d.id for d in store.list_diagnostics(saved.project_id)] == [second.id, saved.id]
    assert store.list_diagnostics(other.id) == []
    assert all(a.status == "todo" for a in store.diagnostic_state(saved.project_id, saved.id)[1])
    assert all(a.status == "todo" for a in store.diagnostic_state(saved.project_id, second.id)[1])


def test_failed_reorder_rolls_back_every_position_and_date(store, saved):
    before = store.diagnostic_state(saved.project_id, saved.id)
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER fail_move BEFORE UPDATE OF position ON actions WHEN NEW.position = 2
                              BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.move_action(saved.project_id, saved.id, before[1][-1].id, 0)
    assert store.diagnostic_state(saved.project_id, saved.id) == before


def test_concurrent_moves_preserve_complete_order(store, saved):
    actions = store.diagnostic_state(saved.project_id, saved.id)[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(store.move_action, saved.project_id, saved.id, a.id, 0) for a in actions[-2:]]
        for task in tasks:
            task.result()
    after = store.diagnostic_state(saved.project_id, saved.id)[1]
    assert {a.id for a in after} == {a.id for a in actions}
    assert [a.position for a in after] == list(range(len(actions)))


def test_history_lists_metadata_even_if_one_snapshot_is_corrupt(store, saved):
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE diagnostics SET snapshot_json = ? WHERE id = ?", ('FAKE_SECRET', saved.id))
    assert store.list_diagnostics(saved.project_id)[0].id == saved.id
    with pytest.raises(ValueError, match="illisible"):
        store.diagnostic_state(saved.project_id, saved.id)
