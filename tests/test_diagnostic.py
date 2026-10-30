from dataclasses import replace
import json
from pathlib import Path

import pytest

from devops_assist.calms import calculate_scores, load_questionnaire
from devops_assist.diagnostic import DiagnosticSnapshot, build_diagnostic, calms_findings
from devops_assist.rules import evaluate_files
from devops_assist.scanner import ScanResult, scan_local, ScanError

FIXTURES = Path(__file__).parent / "fixtures"


def scan(files=None):
    files = files or {}
    return ScanResult("zip", tuple(sorted(files)), evaluate_files(files))


def answers(value):
    return {q: value for q in load_questionnaire().question_ids}


def test_no_inputs_and_rejected_scan_are_refused(tmp_path):
    with pytest.raises(ValueError, match="questionnaire"):
        build_diagnostic()
    with pytest.raises(ScanError):
        scan_local(tmp_path / "does-not-exist")
    with pytest.raises(ValueError, match="rejeté"):
        build_diagnostic(scan={"error": "rejected"})
    with pytest.raises(ValueError, match="incomplet"):
        build_diagnostic(scan=replace(scan(), findings=()))


@pytest.mark.parametrize(("values", "status", "score"), [
    ({}, "unknown", None), ({"C1": 0}, "unknown", 0),
    ({"C1": 3}, "unknown", 100), ({"C1": 0, "C2": 2}, "risk", 100 / 3),
    ({"C1": 0, "C2": 3}, "observed", 50),
    ({"C1": 3, "C2": 3, "C3": 3}, "observed", 100),
])
def test_calms_coverage_and_exact_threshold(values, status, score):
    data = build_diagnostic(answers=values).to_dict()
    finding = data["findings"][0]
    assert finding["status"] == status
    assert finding["source"] == "déclaré"
    assert data["scores"][0]["score"] == (pytest.approx(score) if score is not None else None)
    assert finding["severity"] is None
    assert len(finding["evidence"]) == 3
    assert all(f["status"] == "not_applicable" for f in data["findings"][5:])


@pytest.mark.parametrize("dimension", "CALMS")
def test_each_calms_dimension_has_an_action_for_low_sufficient_coverage(dimension):
    data = build_diagnostic(answers={f"{dimension}1": 0, f"{dimension}2": 1}).to_dict()
    finding = next(f for f in data["findings"] if f["rule_id"] == f"CAL-{dimension}")
    assert finding["status"] == "risk"
    action = next(r for r in data["recommendations"] if r["id"] in finding["recommendation_ids"])
    assert action["kind"] == "risk"
    assert action["source_finding_ids"] == [finding["id"]]
    assert (action["impact"], action["effort"], action["severity"]) == (2, 2, None)


def test_all_unknown_is_submitted_not_skipped():
    data = build_diagnostic(answers={}).to_dict()
    assert len(data["scores"]) == 5
    assert all(s["score"] is None and s["coverage"] == 0 for s in data["scores"])
    assert len(data["recommendations"]) == 5
    assert all(r["kind"] == "verify" and r["severity"] is None for r in data["recommendations"])
    skipped = build_diagnostic(scan=scan()).to_dict()
    assert skipped["scores"] == []
    assert all(f["status"] == "not_applicable" for f in skipped["findings"][:5])


def test_scanned_empty_scope_is_not_skipped_or_absence_proof():
    data = build_diagnostic(scan=scan()).to_dict()
    assert len(data["findings"]) == 11
    by_rule = {f["rule_id"]: f for f in data["findings"]}
    assert by_rule["CI-001"]["status"] == "not_observed"
    assert by_rule["CI-002"]["status"] == "not_applicable"
    assert "non observée" in by_rule["CI-001"]["explanation"]
    assert len(data["recommendations"]) == 2


@pytest.mark.parametrize(("ci", "expected", "align"), [
    (None, "not_applicable", True),
    (b"include: external.yml\n", "unknown", True),
    (b"test: [ broken", "invalid", True),
    (b"include: external.yml\ntest:\n  script: echo ok\n", "observed", False),
])
def test_tests_declared_and_ci_indices_are_reconciled_without_score_penalty(ci, expected, align):
    declared = {"A1": 3, "A2": 3, "A3": 3}
    result = scan({".gitlab-ci.yml": ci} if ci else {})
    data = build_diagnostic(answers=declared, scan=result).to_dict()
    assert next(f for f in data["findings"] if f["rule_id"] == "CI-002")["status"] == expected
    alignment = [a for a in data["alignments"] if a["question_id"] == "A2"]
    assert bool(alignment) == align
    if alignment:
        assert alignment[0]["source_finding_ids"] == ["F-CAL-A", "F-CI-002"]
        assert "Aucune contradiction démontrée" in alignment[0]["limitation"]
    assert data["scores"][1]["score"] == calculate_scores(declared)[1].score == 100
    assert bool([a for a in data["alignments"] if a["question_id"] == "A1"]) == (ci is None)


def test_missing_or_weaker_declaration_does_not_create_alignment():
    assert not build_diagnostic(scan=scan()).to_dict()["alignments"]
    assert not build_diagnostic(answers={"A1": 2, "A2": 2}, scan=scan()).to_dict()["alignments"]


def test_deterministic_snapshot_copies_inputs_and_keeps_versions():
    declared = answers(0)
    result = scan_local(FIXTURES / "incomplete_project")
    first = build_diagnostic(answers=declared, scan=result)
    assert first.to_json() == build_diagnostic(answers=declared, scan=result).to_json()
    declared["C1"] = 3
    data = first.to_dict()
    data["findings"].clear()
    assert len(first.to_dict()["findings"]) == 11
    assert first.to_dict()["inputs"]["calms"]["C1"] == 0
    assert first.to_dict()["versions"] == {
        "engine": "diagnostic-engine-v1", "questionnaire": "calms-v1", "rules": "rules-v2",
        "scanner": "scanner-v2", "recommendations": "recommendations-v1",
    }
    restored = DiagnosticSnapshot.from_json(first.to_json())
    assert restored.to_json() == first.to_json()


def test_historical_scan_versions_remain_readable_without_recalculation(store, monkeypatch):
    original = build_diagnostic(scan=scan_local(FIXTURES / "incomplete_project")).to_dict()
    original["versions"].update(scanner="scanner-v1", rules="rules-v1")
    historical = DiagnosticSnapshot.from_json(json.dumps(original))
    project = store.create_project("Historique v1")
    saved = store.save_diagnostic(project.id, "historical-v1", historical)
    def forbidden(*args, **kwargs):
        pytest.fail("Un diagnostic historique a été recalculé")
    monkeypatch.setattr("devops_assist.rules.evaluate_files", forbidden)
    monkeypatch.setattr("devops_assist.scanner.scan_local", forbidden)
    reloaded = store.get_diagnostic(project.id, saved.id)
    assert reloaded.snapshot.to_dict() == original
    assert reloaded.snapshot.to_dict()["versions"]["rules"] == "rules-v1"


def test_snapshot_has_no_raw_configuration_names_paths_or_image_values():
    marker = "FAKE_SECRET_P4"
    result = scan({
        "Dockerfile": f"FROM registry/{marker}:latest\n".encode(),
        "compose.yaml": f"services:\n  {marker}:\n    privileged: true\n".encode(),
        ".gitlab-ci.yml": f"{marker}:\n  stage: test\n  script: echo {marker}\n".encode(),
    })
    raw = build_diagnostic(answers={}, scan=result).to_json()
    assert marker not in raw
    assert "registry/" not in raw


@pytest.mark.parametrize("raw", ["null", "[]", "{", '{}', '{"schema_version": "diagnostic-v2"}'])
def test_invalid_or_unknown_snapshot_is_refused_neutrally(raw):
    with pytest.raises(ValueError, match="illisible"):
        DiagnosticSnapshot.from_json(raw)


def test_skipped_calms_all_not_applicable():
    assert all(f.status == "not_applicable" and not f.evidence for f in calms_findings(None))


@pytest.mark.parametrize(("section", "field", "value"), [
    ("findings", "evidence", [None]), ("findings", "status", "unexpected"),
    ("findings", "recommendation_ids", ["missing"]), ("findings", "source", []),
    ("recommendations", "priority", float("nan")), ("recommendations", "impact", True),
    ("recommendations", "source_finding_ids", ["missing"]), ("scores", "score", "bad"),
    ("alignments", "explanation", None),
])
def test_corrupt_nested_snapshot_is_rejected_without_current_engine(section, field, value):
    data = build_diagnostic(answers={"A2": 3}, scan=scan()).to_dict()
    data[section][0][field] = value
    with pytest.raises(ValueError, match="illisible"):
        DiagnosticSnapshot.from_json(json.dumps(data))
