import pytest
from devops_flow.calms import calculate_scores
from devops_flow.diagnostic import calms_findings
from devops_flow.profiles import EvaluationProfile
from test_profiles import profile_data


@pytest.mark.parametrize('count', [1,2,3,5,10])
def test_variable_dimensions_scores_and_unknown(count):
    data = profile_data()
    for d in 'CALMS':
        data['calms']['questions'][d] = [{'id':f'{d.lower()}{i}','text':'Pratique ?'} for i in range(count)]
    profile = EvaluationProfile.from_dict(data)
    answers = {q:3 for q in profile.question_ids if q.startswith('c')}
    scores = calculate_scores(answers,profile=profile)
    assert scores[0].score == 100 and scores[0].coverage == 1 and scores[0].total == count
    assert all(s.score is None for s in scores[1:])
    findings = calms_findings(answers,profile=profile)
    assert findings[0].status == 'observed'
    assert all(f.status == 'unknown' for f in findings[1:])


def test_threshold_boundary_and_coverage_precedence():
    data = profile_data()
    data['calms'].update(risk_threshold=60,min_coverage=1/3)
    p = EvaluationProfile.from_dict(data)
    assert calms_findings({'c1':1},profile=p)[0].status == 'risk'
    assert calms_findings({'c1':2},profile=p)[0].status == 'observed'
    data['calms']['min_coverage'] = 1
    assert calms_findings({'c1':1},profile=EvaluationProfile.from_dict(data))[0].status == 'unknown'
    data['calms']['min_coverage'] = 0
    assert calms_findings({},profile=EvaluationProfile.from_dict(data))[0].status == 'unknown'
