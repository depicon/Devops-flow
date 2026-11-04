from devops_flow.profiles import EvaluationProfile
from devops_flow.rules import evaluate_files, select_findings
from devops_flow.diagnostic import build_diagnostic
from devops_flow.scanner import ScanResult
from test_profiles import profile_data


def test_disabled_rules_and_weight_overrides():
    data = profile_data()
    data['rules'] = [{'id':'DOC-002','impact':3,'effort':2}]
    profile = EvaluationProfile.from_dict(data)
    findings = evaluate_files({'Dockerfile':b'FROM python:latest\n'})
    assert [f.rule_id for f in select_findings(findings,profile=profile)] == ['DOC-002']
    snapshot = build_diagnostic(scan=ScanResult('zip',('Dockerfile',),findings),profile=profile).to_dict()
    assert {f['rule_id'] for f in snapshot['findings']} == {*(f'CAL-{d}' for d in 'CALMS'),'DOC-002'}
    rec = snapshot['recommendations'][0]
    assert (rec['impact'],rec['effort'],rec['priority']) == (3,2,1.5)


def test_no_enabled_rules_produces_no_technical_recommendations():
    data = profile_data()
    data['rules'] = []
    p = EvaluationProfile.from_dict(data)
    snapshot = build_diagnostic(scan=ScanResult('zip',(),evaluate_files({})),profile=p).to_dict()
    assert len(snapshot['findings']) == 5
    assert snapshot['recommendations'] == []
