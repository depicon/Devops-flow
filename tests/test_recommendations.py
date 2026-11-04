from dataclasses import replace

import pytest

from devops_flow.diagnostic import build_diagnostic
from devops_flow.models import Finding, TECHNICAL_RULE_IDS
from devops_flow.recommendations import load_catalog, recommend
from devops_flow.rules import evaluate_files
from devops_flow.scanner import ScanResult, scan_zip


def finding(rule, status, id=None):
    return Finding(id or f"F-{rule}", rule, "Technique", status, "inconnu", "", (), "")


def test_catalog_covers_all_eleven_rules_and_cannot_be_mutated_globally():
    catalog = load_catalog()
    assert set(catalog["rules"]) == {f"CAL-{d}" for d in "CALMS"} | set(TECHNICAL_RULE_IDS)
    catalog["rules"].clear()
    assert len(load_catalog()["rules"]) == 11


@pytest.mark.parametrize("rule", [f"CAL-{d}" for d in "CALMS"] + list(TECHNICAL_RULE_IDS))
@pytest.mark.parametrize("status", ["unknown", "invalid", "not_observed"])
def test_missing_information_produces_verification_without_invented_severity(rule, status):
    action, = recommend((finding(rule, status),))
    assert action.kind == "verify"
    assert (action.impact, action.effort, action.priority, action.severity) == (1, 1, 1, None)
    assert "correctif certain" in action.description
    assert action.source_finding_ids == (f"F-{rule}",)


@pytest.mark.parametrize("status", ["observed", "not_applicable"])
def test_no_automatic_actions_on_favorable_or_skipped_findings(status):
    assert recommend(tuple(finding(rule, status) for rule in load_catalog()["rules"])) == ()


def test_impact_effort_sort_ties_and_local_merge():
    inputs = (finding("CAL-C", "risk"), finding("DOC-002", "risk"), finding("CMP-001", "risk"),
              finding("DOC-003", "risk"), finding("CI-002", "unknown"),
              finding("CMP-001", "risk", "F-CMP-other"))
    actions = recommend(inputs)
    assert [r.id for r in actions] == ["REC-CMP-001-RISK", "REC-DOC-002-RISK", "REC-DOC-003-RISK", "REC-CAL-C-RISK", "REC-CI-002-VERIFY"]
    assert [r.priority for r in actions] == [3, 2, 2, 1, 1]
    assert actions[0].source_finding_ids == ("F-CMP-001", "F-CMP-other")
    assert [r.severity for r in actions] == ["élevée", "modérée", "modérée", None, None]
    assert recommend(tuple(reversed(inputs))) == actions
    assert recommend(inputs) == actions  # Aucun état entre appels/diagnostics.


def test_bidirectional_links_and_severity_in_combined_snapshot():
    files = {"Dockerfile": b"FROM alpine:latest\n", "compose.yaml": b"services: {app: {privileged: true}}"}
    data = build_diagnostic(answers={"C1": 0, "C2": 0}, scan=ScanResult("zip", tuple(files), evaluate_files(files))).to_dict()
    findings = {f["id"]: f for f in data["findings"]}
    recommendations = {r["id"]: r for r in data["recommendations"]}
    for action in recommendations.values():
        assert action["source_finding_ids"]
        for id in action["source_finding_ids"]:
            assert action["id"] in findings[id]["recommendation_ids"]
            assert findings[id]["severity"] == action["severity"]
    for item in findings.values():
        for id in item["recommendation_ids"]:
            assert item["id"] in recommendations[id]["source_finding_ids"]


def test_mixed_compose_services_keep_risk_priority_through_scan_and_diagnostic():
    import io
    import zipfile
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("compose.yaml", 'services: {explicit: {privileged: true}, invalid: {privileged: "true"}}')
        archive.writestr("compose.yml", 'services: {invalid: invalid}')
    snapshot = build_diagnostic(scan=scan_zip(data.getvalue())).to_dict()
    result = next(f for f in snapshot["findings"] if f["rule_id"] == "CMP-001")
    assert result["status"] == "risk"
    assert result["severity"] == "élevée"
    action = snapshot["recommendations"][0]
    assert (action["id"], action["priority"]) == ("REC-CMP-001-RISK", 3)
    assert action["source_finding_ids"] == [result["id"]]
    assert [e["code"] for e in result["evidence"]] == ["privileged_true", "invalid_compose", "invalid_compose"]
