from dataclasses import replace
from itertools import combinations
import json

import pytest

from devops_flow.diagnostic import DiagnosticSnapshot, build_diagnostic
from devops_flow.diagram import (STATUS_COLORS, STAGES, aggregate_status, build_flow,
                                   render_mermaid, snapshot_to_mermaid)
from devops_flow.profiles import EvaluationProfile, baseline_profile
from devops_flow.rules import evaluate_files
from devops_flow.scanner import ScanResult


def scan(files):
    return ScanResult('zip', tuple(sorted(files)), evaluate_files(files))


@pytest.fixture(params=['calms', 'scan', 'combined', 'unknown', 'services'])
def snapshot(request):
    files = {'Dockerfile': b'FROM alpine:3\nUSER app\n',
             '.gitlab-ci.yml': b'build: {script: echo build}\ntest: {script: echo test}\ndeploy: {script: echo deploy}\n'}
    if request.param == 'services':
        files['compose.yaml'] = b'services: {db: {image: postgres:17}, cache: {image: redis:7}}'
    answers = None if request.param == 'scan' else ({} if request.param == 'unknown' else {q.lower(): 3 for _, _, qs, _, _ in STAGES for q in qs})
    return build_diagnostic(answers=answers, scan=None if request.param in {'calms', 'unknown'} else scan(files), profile=baseline_profile())


def test_projection_is_deterministic_sourced_and_preserves_snapshot(snapshot):
    before = snapshot.to_json()
    flow = build_flow(snapshot)
    mermaid = render_mermaid(flow)
    assert mermaid == snapshot_to_mermaid(snapshot) == render_mermaid(build_flow(snapshot))
    assert mermaid.startswith('flowchart LR\n') and 'feedback -.-> code' in mermaid
    assert 'subgraph culture["Culture · transverse"]\n        direction LR' in mermaid
    assert [n.id for n in flow.nodes[:8]] == [s[0] for s in STAGES]
    assert [(e.source, e.target) for e in flow.edges[:7]] == [(STAGES[i][0], STAGES[i + 1][0]) for i in range(7)]
    findings = {f['id'] for f in snapshot.to_dict()['findings']}
    questions = set(snapshot.to_dict()['profile']['calms']['questions'][d][i]['id']
                    for d in 'CALMS' for i in range(3))
    for node in flow.nodes:
        assert node.annotations
        for source in node.annotations:
            assert source.finding_id in findings or source.question_id in questions or source.profile_ref == 'baseline:1'
        assert node.status == aggregate_status(a.status for a in node.annotations)
    assert flow.culture.finding_id == 'F-CAL-C'
    assert snapshot.to_json() == before
    copy = flow.to_dict()
    copy['nodes'].clear()
    assert flow.nodes


def test_all_priority_combinations():
    priority = ['risk', 'invalid', 'unknown', 'not_observed', 'observed', 'not_applicable']
    assert aggregate_status([]) == 'unknown'
    for length in range(1, len(priority) + 1):
        for values in combinations(priority, length):
            assert aggregate_status(values) == aggregate_status(reversed(values)) == values[0]
    with pytest.raises(ValueError):
        aggregate_status(['absent'])


@pytest.mark.parametrize('value,threshold,expected', [(0, 50, 'risk'), (1, 50, 'risk'), (2, 50, 'observed'),
                                                     (3, 50, 'observed'), ('unknown', 50, 'unknown'), (0, 0, 'observed')])
def test_response_status_is_distinct_from_dimension(value, threshold, expected):
    profile = replace(baseline_profile(), risk_threshold=threshold)
    snapshot = build_diagnostic(answers={'a2': value}, profile=profile)
    node = next(n for n in build_flow(snapshot).nodes if n.id == 'test')
    annotation = next(a for a in node.annotations if a.question_id)
    assert annotation.status == expected and node.status == expected
    assert next(f for f in snapshot.to_dict()['findings'] if f['rule_id'] == 'CAL-A')['status'] == 'unknown'


def test_custom_profile_never_infers_semantics_from_same_ids():
    profile = replace(baseline_profile(), slug='custom', rules=())
    snapshot = build_diagnostic(answers={'a2': 3}, profile=profile)
    flow = build_flow(snapshot)
    assert all(n.status == ('not_applicable' if n.id in {'build', 'package', 'operate'} else 'unknown') for n in flow.nodes)
    assert all(a.profile_ref == 'custom:1' for n in flow.nodes for a in n.annotations)
    assert all(a.finding_id is None and a.question_id is None for n in flow.nodes for a in n.annotations)


def test_custom_profile_skipped_questionnaire_remains_not_applicable():
    profile = replace(baseline_profile(), slug='custom', rules=())
    flow = build_flow(build_diagnostic(scan=scan({}), profile=profile))
    assert all(n.status == 'not_applicable' for n in flow.nodes)


def test_job_test_is_never_build_or_deploy_evidence():
    snapshot = build_diagnostic(scan=scan({'.gitlab-ci.yml': b'test: {script: echo test}'}))
    nodes = {n.id: n for n in build_flow(snapshot).nodes}
    assert nodes['test'].status == 'observed'
    assert [e.code for a in nodes['test'].annotations if a.rule_id == 'CI-002' for e in a.evidence] == ['local_test_job']
    for stage in ('build', 'deploy'):
        source = next(a for a in nodes[stage].annotations if a.rule_id == 'CI-002')
        assert source.status == 'unknown'
        assert [e.code for e in source.evidence] == [stage + '_job_unconfirmed']


@pytest.mark.parametrize('content,expected', [(b'[broken', 'invalid'), (b'include: remote.yml', 'unknown'),
                                            (b'build: {script: echo ok}\ndeploy: {script: echo ok}', 'observed')])
def test_stage_specific_ci_statuses(content, expected):
    flow = build_flow(build_diagnostic(scan=scan({'.gitlab-ci.yml': content})))
    for node in flow.nodes:
        if node.id in {'build', 'deploy'}:
            assert next(a.status for a in node.annotations if a.rule_id == 'CI-002') == expected


def test_legacy_without_stage_or_service_proofs_is_not_enriched(monkeypatch):
    data = build_diagnostic(scan=scan({'.gitlab-ci.yml': b'build: {script: echo ok}\ntest: {script: echo ok}',
                                      'compose.yaml': b'services: {db: {image: postgres:17}}'})).to_dict()
    for finding in data['findings']:
        finding['evidence'] = [e for e in finding['evidence'] if not e['code'].startswith(('service_type_', 'local_build_', 'build_job_', 'deploy_job_'))]
    data['versions'].update(scanner='scanner-v2', rules='rules-v2')
    old = DiagnosticSnapshot.from_json(json.dumps(data))
    def forbidden(*args, **kwargs):
        pytest.fail('Projection historique : moteur/scanner/catalogue appelé')
    monkeypatch.setattr('devops_flow.diagnostic.load_questionnaire', forbidden)
    monkeypatch.setattr('devops_flow.diagnostic.calculate_scores', forbidden)
    monkeypatch.setattr('devops_flow.rules.evaluate_files', forbidden)
    flow = build_flow(old)
    assert len(flow.nodes) == 8
    assert next(a.status for n in flow.nodes if n.id == 'build' for a in n.annotations if a.rule_id == 'CI-002') == 'unknown'
    assert old.to_dict() == data


def test_services_are_deduplicated_and_keep_each_controlled_file_reference():
    flow = build_flow(build_diagnostic(scan=scan({
        'compose.yaml': b'services: {one: {image: postgres:17}, two: {image: postgres:16}, raw: {image: private/FAKE_SECRET:latest}}',
        'compose.yml': b'services: {other: {image: docker.io/library/postgres:17}, cache: {image: redis:7, privileged: true}}'})))
    services = {n.id: n for n in flow.nodes if n.kind == 'service'}
    assert set(services) == {'service_postgres', 'service_redis', 'service_unresolved'}
    assert services['service_unresolved'].status == 'unknown'
    assert services['service_redis'].status == 'observed'  # type détecté, aucun transfert de risque global
    assert len(services['service_postgres'].annotations[0].evidence) == 2
    assert 'FAKE_SECRET' not in json.dumps(flow.to_dict()) + render_mermaid(flow)
    assert 'operate -.-> service_postgres' in render_mermaid(flow)
    assert render_mermaid(flow).index('    end') < render_mermaid(flow).index('    service_postgres["')


def test_mermaid_handles_multiple_dimensions_and_legend_without_removing_nodes(snapshot):
    flow = build_flow(snapshot)
    assert next(n.dimensions for n in flow.nodes if n.id == 'test') == ('A', 'L')
    marked = render_mermaid(flow, highlighted_status='unknown')
    for node in flow.nodes:
        assert f'{node.id}["' in marked
    with pytest.raises(ValueError):
        render_mermaid(flow, highlighted_status='evil')
    with pytest.raises(ValueError):
        render_mermaid(replace(flow, nodes=(replace(flow.nodes[0], id='x"]\nevil'),)))


def test_palette_meets_normal_text_aa():
    def luminance(hex_color):
        channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        return sum(weight * (c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4)
                   for c, weight in zip(channels, (.2126, .7152, .0722)))
    for fill, color, _ in STATUS_COLORS.values():
        low, high = sorted((luminance(fill), luminance(color)))
        assert (high + .05) / (low + .05) >= 4.5
