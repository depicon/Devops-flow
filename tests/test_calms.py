import json

import pytest

from devops_flow.calms import (
    CalmsDraft, UNKNOWN, calculate_scores, load_questionnaire, normalize_answers,
)
from devops_flow.db import ProjectStore


def test_versioned_catalogue_has_fifteen_unique_questions_in_five_dimensions():
    questionnaire = load_questionnaire()
    assert questionnaire.version == "calms-v1"
    assert [d.id for d in questionnaire.dimensions] == list("CALMS")
    assert all(len(d.questions) == 3 for d in questionnaire.dimensions)
    assert len(set(questionnaire.question_ids)) == 15
    assert all(q.text for d in questionnaire.dimensions for q in d.questions)


@pytest.mark.parametrize(("answer", "expected"), [(0, 0.0), (3, 100.0)])
def test_complete_extremes(answer, expected):
    scores = calculate_scores({key: answer for key in load_questionnaire().question_ids})
    assert len(scores) == 5
    assert all(s.score == expected and s.coverage == 1 and s.answered == 3 for s in scores)


def test_unknown_is_excluded_and_precision_is_preserved():
    scores = calculate_scores({"C1": 0, "C2": 1, "C3": UNKNOWN, "A1": 3})
    assert scores[0].score == pytest.approx(100 / 6)
    assert scores[0].coverage == pytest.approx(2 / 3)
    assert scores[1].score == 100
    assert scores[1].coverage == pytest.approx(1 / 3)
    assert scores[2].score is None
    assert scores[2].coverage == 0
    assert "insuffisante" in scores[1].interpretation


def test_missing_and_all_unknown_remain_indeterminate():
    missing = calculate_scores({})
    explicit = calculate_scores({key: UNKNOWN for key in load_questionnaire().question_ids})
    assert missing == explicit
    assert all(s.score is None and s.answered == 0 and s.coverage == 0 for s in missing)
    assert all("indéterminée" in s.interpretation for s in missing)


@pytest.mark.parametrize("value", [-1, 4, 1.0, True, False, None, "1", "inconnu", [], {}])
def test_invalid_answers_are_not_silently_converted(value):
    with pytest.raises(ValueError):
        calculate_scores({"C1": value})


@pytest.mark.parametrize("answers", [{"unexpected": 2}, [], None])
def test_invalid_question_ids_or_payload_rejected(answers):
    with pytest.raises(ValueError):
        normalize_answers(answers)


def test_normalization_does_not_mutate_inputs():
    answers = {"C1": 3}
    normalized = normalize_answers(answers)
    assert answers == {"C1": 3}
    assert len(normalized) == 15
    assert normalized["C2"] == UNKNOWN


@pytest.mark.parametrize("notes", [{"C1": "a" * 2001}, {"C1": 2}, {"X1": "texte"}, []])
def test_invalid_notes_rejected(notes):
    with pytest.raises(ValueError):
        CalmsDraft.new({}, notes)


def test_notes_do_not_change_scores_and_round_trip_preserves_french():
    draft = CalmsDraft.new({"C1": 1}, {"C1": "  Retour d'équipe  ", "C2": " "})
    assert draft.notes == {"C1": "Retour d'équipe"}
    assert CalmsDraft.from_json(draft.to_json()) == draft
    assert calculate_scores(draft.answers) == calculate_scores({"C1": 1})


@pytest.mark.parametrize("raw", ["bad-json-FAKE_SECRET", "[]", "{}"])
def test_unreadable_draft_has_neutral_error(raw):
    with pytest.raises(ValueError, match="illisible") as error:
        CalmsDraft.from_json(raw)
    assert "FAKE_SECRET" not in str(error.value)


@pytest.mark.parametrize(("field", "value"), [
    ("version", "future-version"), ("answers", {"C1": True}),
    ("saved_at", "2026-10-20T00:00:00"), ("saved_at", None),
])
def test_draft_with_unknown_version_or_invalid_fields_is_rejected(field, value):
    data = json.loads(CalmsDraft.new({}).to_json())
    data[field] = value
    with pytest.raises(ValueError, match="illisible"):
        CalmsDraft.from_json(json.dumps(data))


def test_scores_interpretation_obeys_coverage_before_threshold():
    sparse = calculate_scores({"C1": 0})[0]
    low = calculate_scores({"C1": 0, "C2": 0})[0]
    boundary = calculate_scores({"C1": 0, "C2": 3})[0]
    assert "insuffisante" in sparse.interpretation
    assert "peu systématisées" in low.interpretation
    assert boundary.score == 50
    assert "peu systématisées" not in boundary.interpretation


def test_saved_draft_survives_reopen_and_is_isolated(store, db_path):
    first = store.create_project("Équipe A")
    second = store.create_project("Équipe B")
    saved = store.save_calms(first.id, {"C1": 2, "A2": 3}, {"C1": "Précision locale"})
    reopened = ProjectStore(db_path)
    reopened.initialize()
    assert reopened.load_calms(first.id) == saved
    assert reopened.load_calms(second.id) is None
    assert saved.answers["C2"] == UNKNOWN
    assert saved.version == "calms-v1"
    assert reopened.get_project(first.id).updated_at == saved.saved_at


def test_explicit_save_updates_only_its_project_and_invalid_update_preserves_draft(store):
    first = store.create_project("A")
    second = store.create_project("B")
    store.save_calms(first.id, {"C1": 1})
    other = store.save_calms(second.id, {"C1": 3})
    current = store.save_calms(first.id, {"C1": 2}, {"C1": "Note"})
    with pytest.raises(ValueError):
        store.save_calms(first.id, {"C1": 4})
    assert store.load_calms(first.id) == current
    assert store.load_calms(second.id) == other
    assert len(store.list_projects()) == 2


def test_nonexistent_project_cannot_be_saved_or_loaded(store):
    with pytest.raises(ValueError, match="projet"):
        store.save_calms("absent", {})
    with pytest.raises(ValueError, match="projet"):
        store.load_calms("absent")
