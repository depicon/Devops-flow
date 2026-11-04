import json
import pytest
from devops_flow.diagnostic import build_diagnostic, DiagnosticSnapshot
from devops_flow.profiles import EvaluationProfile, baseline_profile
from devops_flow.scanner import ScanResult
from devops_flow.rules import evaluate_files
from test_profiles import profile_data


def test_snapshot_profile_and_history_without_catalogue(store,monkeypatch):
    p = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    project = store.create_project('Test',profile_slug=p.slug)
    stored = store.save_diagnostic(project.id,'s',build_diagnostic(answers={},profile=p))
    store.publish_profile(p,expected_version=1)
    def forbidden(*args,**kwargs):
        raise AssertionError('consultation courante interdite')
    monkeypatch.setattr(store,'get_profile',forbidden)
    monkeypatch.setattr('devops_flow.calms.load_questionnaire',forbidden)
    result = store.get_diagnostic(project.id,stored.id).snapshot.to_dict()
    assert result['schema_version'] == 'diagnostic-v2'
    assert result['profile'] == p.to_dict()
    assert result['profile']['version'] == 1
    with pytest.raises(ValueError,match='référencé'):
        store.archive_profile(p.slug)


def test_unknown_or_mismatched_profile_refused(store):
    p = EvaluationProfile.from_dict(profile_data())
    project = store.create_project('Base')
    with pytest.raises(ValueError,match='profil'):
        store.save_diagnostic(project.id,'s',build_diagnostic(answers={},profile=p))
    with pytest.raises(ValueError,match='profil'):
        build_diagnostic(answers={},profile='unknown')
    store.publish_profile(p)
    store.update_project(project.id,'Base','',profile_slug=p.slug,profile_version=1)
    with pytest.raises(ValueError,match='profil'):
        store.save_diagnostic(project.id,'s',build_diagnostic(answers={}))


def test_baseline_profile_scores_match_legacy_and_alignment_semantics():
    p = baseline_profile()
    answers = {'A1':3,'A2':3,'C1':0,'C2':0}
    scan = ScanResult('zip',(),evaluate_files({}))
    old = build_diagnostic(answers=answers,scan=scan).to_dict()
    new = build_diagnostic(answers={k.lower():v for k,v in answers.items()},scan=scan,profile=p).to_dict()
    assert old['scores'] == new['scores']
    assert [f['status'] for f in old['findings']] == [f['status'] for f in new['findings']]
    assert len(new['alignments']) == len(old['alignments']) == 2
    data = profile_data()
    custom = build_diagnostic(answers={'a1':3,'a2':3},scan=scan,profile=EvaluationProfile.from_dict(data)).to_dict()
    assert custom['alignments'] == []


def test_concurrent_draft_change_refused(store):
    project = store.create_project('Base')
    p = store.project_profile(project.id)
    saved = store.save_calms(project.id,{},profile=p)
    store.save_calms(project.id,{'c1':3},profile=p)
    with pytest.raises(ValueError,match='questionnaire a changé'):
        store.save_diagnostic(project.id,'s',build_diagnostic(answers=saved.answers,profile=p),expected_draft=saved.saved_at.isoformat())
    assert store.list_diagnostics(project.id) == []


def test_snapshot_rejects_question_not_in_embedded_catalogue():
    data = build_diagnostic(answers={},profile=baseline_profile()).to_dict()
    data['inputs']['calms']['unexpected'] = 2
    with pytest.raises(ValueError,match='illisible'):
        DiagnosticSnapshot.from_json(json.dumps(data))
