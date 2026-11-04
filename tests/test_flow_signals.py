import json
from dataclasses import asdict

import pytest

from devops_flow.rules import evaluate_files


@pytest.mark.parametrize('image,kind', [('postgres:17', 'postgres'), ('docker.io/library/redis@sha256:abc', 'redis'),
                                     ('docker.io/mysql:8', 'mysql'), ('library/rabbitmq:4', 'rabbitmq'),
                                     ('memcached', 'cache'), ('mariadb:11', 'database'), ('nats:2', 'queue'),
                                     ('private/FAKE_SECRET:1', 'unresolved'), ('${FAKE_SECRET}', 'unresolved'),
                                     ('private:5000/redis', 'unresolved'), ('postgres-extra', 'unresolved')])
def test_only_known_image_types_cross_boundary(image, kind):
    findings = evaluate_files({'compose.yaml': f'services: {{FAKE_SECRET_NAME: {{image: "{image}"}}}}'.encode()})
    finding = next(f for f in findings if f.rule_id == 'CMP-001')
    assert finding.status == 'observed'
    assert [e.code for e in finding.evidence] == ['no_privileged_true', 'service_type_' + kind]
    assert 'FAKE_SECRET' not in json.dumps(asdict(finding))


def test_compose_partial_invalid_service_keeps_risk_and_valid_type():
    finding = next(f for f in evaluate_files({'compose.yaml': b'services: {db: {image: postgres:17, privileged: true}, bad: []}'}) if f.rule_id == 'CMP-001')
    assert finding.status == 'risk'
    assert [e.code for e in finding.evidence] == ['privileged_true', 'invalid_compose', 'service_type_postgres']


@pytest.mark.parametrize('content', [b'services: {db: {image: postgres}, bad: [}',
                                    b'services: {db: &value {image: postgres}, other: *value}',
                                    b'services: {db: {<<: {image: postgres}}}'])
def test_rejected_yaml_never_invents_a_service(content):
    finding = next(f for f in evaluate_files({'compose.yaml': content}) if f.rule_id == 'CMP-001')
    assert not any(e.code.startswith('service_type_') for e in finding.evidence)


def test_ci_signals_do_not_examine_scripts_or_export_names():
    findings = evaluate_files({'.gitlab-ci.yml': b'FAKE_SECRET_JOB: {stage: build, script: echo FAKE_SECRET}\n.hidden: {stage: deploy, script: echo no}\nother: {script: echo deploy}\ntest: {script: echo ok}'})
    finding = next(f for f in findings if f.rule_id == 'CI-002')
    assert finding.status == 'observed'
    assert [e.code for e in finding.evidence] == ['local_test_job', 'local_build_job', 'deploy_job_unconfirmed']
    assert 'FAKE_SECRET' not in json.dumps(asdict(finding))
