import json
from dataclasses import replace

import pytest

from devops_flow.diagnostic import DiagnosticSnapshot, build_diagnostic
from devops_flow.profiles import baseline_profile
from devops_flow.scanner import scan_project_zip
from tests.test_project_scanner import zipped


def project_scan():
    return scan_project_zip(zipped({'backend/pyproject.toml': '', 'frontend/package.json': '{}',
        'backend/Dockerfile': 'FROM alpine:latest', 'frontend/Dockerfile': 'FROM alpine:latest',
        'compose.yaml': 'services:\n  api:\n    build: backend\n    privileged: true\n  web:\n    build: frontend\n'}))


def test_scoped_findings_recommendations_and_compose_links():
    scan = project_scan()
    backend, frontend = scan.architecture.components
    data = build_diagnostic(scan=scan, profile=baseline_profile()).to_dict()
    assert data['schema_version'] == 'diagnostic-v3'
    assert len([f for f in data['findings'] if f['rule_id'] == 'DOC-002']) == 2
    recs = [r for r in data['recommendations'] if r['rule_id'] == 'DOC-002']
    assert len(recs) == 2 and len({r['id'] for r in recs}) == 2
    assert {r['scope_id'] for r in recs} == {backend.id, frontend.id}
    compose = {f['scope_id']: f['status'] for f in data['findings'] if f['rule_id'] == 'CMP-001'}
    assert compose[backend.id] == 'risk' and compose[frontend.id] == 'observed'
    assert len(data['architecture']['relations']) == 2
    assert DiagnosticSnapshot.from_json(json.dumps(data)).to_dict() == data


def test_root_ci_does_not_automatically_prove_component_tests():
    scan = scan_project_zip(zipped({'backend/package.json': '{}',
        '.gitlab-ci.yml': 'tests:\n  script: echo test\n'}))
    data = build_diagnostic(scan=scan).to_dict()
    cid = scan.architecture.components[0].id
    assert next(f['status'] for f in data['findings'] if f['scope_id'] == cid and f['rule_id'] == 'CI-001') == 'not_observed'


def test_disabled_rules_and_multiple_alignment_sources():
    profile = baseline_profile()
    profile = replace(profile, rules=tuple(r for r in profile.rules if r.id == 'CI-001'))
    data = build_diagnostic(scan=project_scan(), answers={'a1': 3}, profile=profile).to_dict()
    assert {f['rule_id'] for f in data['findings'] if f['category'] == 'Technique'} == {'CI-001'}
    assert len(data['alignments']) == 2
    none = replace(profile, rules=())
    assert len(build_diagnostic(scan=project_scan(), profile=none).to_dict()['findings']) == 5


def test_new_snapshot_read_does_not_call_scanner_engine_catalog(monkeypatch):
    snapshot = build_diagnostic(scan=project_scan())
    def forbidden(*a, **k):
        raise AssertionError('Recalcul historique')
    monkeypatch.setattr('devops_flow.diagnostic.build_diagnostic', forbidden)
    monkeypatch.setattr('devops_flow.recommendations.load_catalog', forbidden)
    monkeypatch.setattr('devops_flow.signals.registry.default_registry', forbidden)
    assert DiagnosticSnapshot.from_json(snapshot.to_json()) == snapshot


@pytest.mark.parametrize('mutation', ['scope', 'path', 'recommendation', 'confidence', 'duplicate', 'missing_rule'])
def test_corrupt_new_snapshots_rejected(mutation):
    data = build_diagnostic(scan=project_scan()).to_dict()
    if mutation == 'scope': data['findings'][-1]['scope_id'] = 'missing'
    if mutation == 'path': data['inputs']['scan']['files'].append('../secret')
    if mutation == 'recommendation': data['recommendations'][0]['source_finding_ids'] = ['bad']
    if mutation == 'confidence': data['architecture']['components'][0]['signals'][0]['confidence'] = 'certain'
    if mutation == 'duplicate': data['findings'].append(data['findings'][-1])
    if mutation == 'missing_rule': data['findings'] = [f for f in data['findings'] if f['rule_id'] != 'DOC-001']
    with pytest.raises(ValueError): DiagnosticSnapshot.from_json(json.dumps(data))
