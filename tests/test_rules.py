from dataclasses import asdict
import json

import pytest

from devops_assist.rules import (
    CI_RESERVED, InvalidConfiguration, _yaml_document, evaluate_files,
)


def finding(name, content, rule):
    files = {name: content.encode() if isinstance(content, str) else content}
    return next(f for f in evaluate_files(files) if f.rule_id == rule)


def test_six_rules_without_files_are_neutral():
    results = evaluate_files({})
    assert [f.rule_id for f in results] == ["DOC-001", "DOC-002", "DOC-003", "CMP-001", "CI-001", "CI-002"]
    assert [f.status for f in results] == ["not_observed", "not_applicable", "not_applicable", "not_applicable", "not_observed", "not_applicable"]
    assert all(f.source == "inconnu" for f in results)


@pytest.mark.parametrize("content,status", [
    ("FROM alpine:latest\nUSER app", "risk"),
    ("FROM alpine:3\nUSER app", "observed"),
    ("# FROM alpine:latest\nFROM alpine:3", "observed"),
    ("FROM --platform=linux/amd64 alpine:latest AS build\nFROM scratch", "risk"),
    ("ARG IMAGE\nFROM ${IMAGE}", "unknown"),
    ("FROM ${IMAGE}:latest", "risk"),
    ("FROM alpine:latest@sha256:abc", "observed"),
    ("FROM alpine\nUSER app", "observed"),
    ("FROM \\\n alpine:latest", "risk"),
    ("# escape=`\nFROM alpine:latest", "unknown"),
    ("FROM alpine\nRUN <<EOF\necho hi\nEOF", "unknown"),
    ("FROM alpine extra tokens", "invalid"),
    ("USER app\nFROM alpine", "invalid"),
    ("FROM", "invalid"), ("# only comments", "invalid"),
    (b"FROM \xff", "invalid"),
])
def test_docker_images(content, status):
    assert finding("Dockerfile", content, "DOC-002").status == status


@pytest.mark.parametrize("content,status", [
    ("FROM alpine\nUSER app", "observed"),
    ("FROM alpine\nUSER root", "observed"),
    ("FROM alpine\n# USER app", "risk"),
    ("FROM alpine AS build\nUSER app\nFROM scratch", "risk"),
    ("FROM alpine AS build\nFROM scratch\nUSER app", "observed"),
    ("FROM alpine\nONBUILD USER app", "risk"),
    ("not a dockerfile", "unknown"),
])
def test_user_only_in_final_stage(content, status):
    assert finding("Dockerfile", content, "DOC-003").status == status


def test_docker_line_numbers_and_presence():
    results = evaluate_files({"Dockerfile": b"# first\nFROM alpine:latest\nUSER app\n"})
    assert results[0].status == "observed"
    assert results[1].evidence[0].line == 2
    assert results[2].evidence[0].line == 3


@pytest.mark.parametrize("content,status", [
    ("services: {app: {privileged: true}}", "risk"),
    ("services: {app: {privileged: false}}", "observed"),
    ("services: {app: {}} # privileged: true", "observed"),
    ('services: {app: {privileged: "true"}}', "invalid"),
    ("services: {app: {privileged: 1}}", "invalid"),
    ("services: {app: {privileged: null}}", "invalid"),
    ("services: {app: {extends: other}}", "unknown"),
    ("include: other.yaml\nservices: {app: {}}", "unknown"),
    ("include: other.yaml\nservices: {app: {privileged: true}}", "risk"),
    ("services: {app: {<<: {privileged: false}}}", "unknown"),
    ("services: []", "invalid"), ("services: {app: []}", "invalid"),
    ("{}", "invalid"), ("services: {", "invalid"),
    ("services: {app: {privileged: false, privileged: true}}", "invalid"),
    ("services: {app: !!python/object/apply:os.system ['echo']}", "invalid"),
    ("services: &root {app: *root}", "invalid"),
    ("services: {app: &value {}, second: *value}", "invalid"),
    (b"\xff", "invalid"),
])
def test_compose(content, status):
    assert finding("compose.yaml", content, "CMP-001").status == status


def test_compose_variants_are_independent_and_keep_partial_evidence():
    files = {"compose.yaml": b"services: {one: {privileged: true}}", "compose.yml": b"[broken"}
    result = next(f for f in evaluate_files(files) if f.rule_id == "CMP-001")
    assert result.status == "risk"
    assert [e.code for e in result.evidence] == ["privileged_true", "invalid_compose"]


@pytest.mark.parametrize("other", ['{privileged: "true"}', 'invalid', '[]', 'null', '{privileged: 1}'])
@pytest.mark.parametrize("invalid_first", [False, True])
@pytest.mark.parametrize("explicit", [True, False])
def test_compose_mixed_services_keep_explicit_risk_and_partial_evidence(other, invalid_first, explicit):
    services = [f"  explicit: {{privileged: {str(explicit).lower()}}}", f"  FAKE_SECRET_SERVICE: {other}"]
    if invalid_first:
        services.reverse()
    content = "services:\n" + "\n".join(services)
    result = finding("compose.yaml", content, "CMP-001")
    assert result.status == ("risk" if explicit else "invalid")
    assert [e.code for e in result.evidence] == (["privileged_true", "invalid_compose"] if explicit else ["invalid_compose"])
    assert "FAKE_SECRET" not in json.dumps(asdict(result))


def test_compose_partial_risk_keeps_unresolved_extension_evidence():
    result = finding("compose.yaml", 'services: {one: {privileged: true}, other: {extends: base}}', "CMP-001")
    assert result.status == "risk"
    assert [e.code for e in result.evidence] == ["privileged_true", "compose_unresolved"]


@pytest.mark.parametrize("content,status", [
    ("test: {script: echo ok}", "observed"),
    ("TESTS: {script: [echo ok]}", "observed"),
    ("unit: {stage: test, script: echo ok}", "observed"),
    ("include: external.yml\nunit: {stage: test, script: echo ok}", "observed"),
    ("test: {extends: .base}", "unknown"),
    ("test: {extends: .base, script: echo ok}", "observed"),
    (".test: {stage: test, script: echo ok}", "unknown"),
    ("unit_tests: {script: pytest}", "unknown"),
    ("include: external.yml", "unknown"),
    ("{}", "unknown"),
    ("test: {script: 7}", "invalid"),
    ("test: {script: []}", "invalid"),
    ("test: {script: [false]}", "invalid"),
    ("test: {script: [[echo ok]]}", "unknown"),
    ("test: {stage: [test], script: echo ok}", "invalid"),
    ("stages: test", "invalid"), ("variables: []", "invalid"),
    ("test: echo ok", "invalid"),
    ("test: {script: echo ok}\ntest: {script: echo duplicate}", "invalid"),
    ("test: {script: echo ok}\n---\n{}", "invalid"),
    ("test: [broken", "invalid"),
])
def test_ci_local_tests_only(content, status):
    result = finding(".gitlab-ci.yml", content, "CI-002")
    assert result.status == status
    assert "non résolus" in result.limitation


@pytest.mark.parametrize("reserved", sorted(CI_RESERVED))
def test_ci_reserved_keys_are_not_jobs(reserved):
    # Un mapping script est toléré pour les clés réservées de type mapping/inconnues,
    # les autres restent invalides ; aucune ne devient un job de test.
    assert finding(".gitlab-ci.yml", f"{reserved}: {{stage: test, script: echo ok}}", "CI-002").status != "observed"


def test_yaml_depth_boundary():
    assert _yaml_document(("x: " + "[" * 31 + "0" + "]" * 31).encode())
    with pytest.raises(InvalidConfiguration):
        _yaml_document(("x: " + "[" * 32 + "0" + "]" * 32).encode())


def test_yaml_node_boundary():
    assert _yaml_document(("x: [" + ",".join("0" for _ in range(9997)) + "]").encode())
    with pytest.raises(InvalidConfiguration):
        _yaml_document(("x: [" + ",".join("0" for _ in range(9998)) + "]").encode())


def test_excessive_integer_is_neutral():
    result = finding("compose.yaml", "services: {app: {privileged: " + "9" * 5000 + "}}", "CMP-001")
    assert result.status == "invalid"


@pytest.mark.parametrize("content", ["? [a,b]\n: c", "{1: a}", "{x: a, x: b}", "[1,2]", ""])
def test_yaml_invalid_shapes(content):
    with pytest.raises(InvalidConfiguration):
        _yaml_document(content.encode())


def test_findings_never_contain_free_values_or_parser_exceptions():
    marker = "FAKE_SECRET_CONFIG_SENTINEL"
    files = {
        "Dockerfile": f"FROM private/{marker}:latest\nUSER {marker}".encode(),
        "compose.yaml": f"services: {{{marker}: {{privileged: true, environment: [{marker}]}}}}".encode(),
        ".gitlab-ci.yml": f"{marker}: {{stage: test, script: echo {marker}}}".encode(),
    }
    for replacement in (None, b"broken: [FAKE_SECRET_CONFIG_SENTINEL"):
        if replacement:
            files["compose.yaml"] = replacement
            files[".gitlab-ci.yml"] = replacement
        exported = json.dumps([asdict(f) for f in evaluate_files(files)])
        assert marker not in exported
        assert "private/" not in exported
