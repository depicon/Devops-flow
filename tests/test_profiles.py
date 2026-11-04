from dataclasses import FrozenInstanceError
import pytest

from devops_flow.profiles import EvaluationProfile, baseline_profile, changed_sections


def profile_data():
    data = baseline_profile().to_dict()
    data.update(slug='equipe-data', name='Équipe data', conventions='## Pratiques\n\nRevue des changements.\n')
    return data


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(slug='../bad'), lambda d: d.update(slug='A'),
    lambda d: d.update(slug='a'*65), lambda d: d.update(slug='new'),
    lambda d: d.update(version=True), lambda d: d.update(version=0), lambda d: d.update(version=10**50),
    lambda d: d.update(author='a'*121), lambda d: d.update(name=''),
    lambda d: d.update(description='a'*2001), lambda d: d.update(created_at='2026-01-01'),
    lambda d: d.update(unexpected='value'),
    lambda d: d['calms']['questions'].pop('C'),
    lambda d: d['calms']['questions'].update(C=[]),
    lambda d: d['calms']['questions'].update(C=d['calms']['questions']['C']*4),
    lambda d: d['calms']['questions']['C'][0].update(id='C1'),
    lambda d: d['calms']['questions']['C'][0].update(id='a1'),
    lambda d: d['calms']['questions']['C'][0].update(text=''),
    lambda d: d['calms'].update(risk_threshold=float('nan')),
    lambda d: d['calms'].update(risk_threshold=101), lambda d: d['calms'].update(risk_threshold=10**1000),
    lambda d: d['calms'].update(min_coverage=-1),
    lambda d: d['calms'].update(min_coverage=True),
    lambda d: d.update(rules=[{'id':'NOPE'}]),
    lambda d: d.update(rules=[{'id':'DOC-001','impact':True}]),
    lambda d: d.update(rules=[{'id':'DOC-001','effort':0}]),
    lambda d: d.update(rules=[{'id':'DOC-001'}]*2),
    lambda d: d.update(conventions='&lt;script&gt;'), lambda d: d.update(conventions='\ud800'),
    lambda d: d.update(conventions='é'*70000),
])
def test_reject_invalid_profiles(mutation):
    data = profile_data()
    mutation(data)
    with pytest.raises(ValueError, match='Profil invalide'):
        EvaluationProfile.from_dict(data)


def test_profile_is_immutable_and_returns_independent_data():
    profile = EvaluationProfile.from_dict(profile_data())
    with pytest.raises(FrozenInstanceError):
        profile.name = 'changed'
    data = profile.to_dict()
    data['calms']['questions']['C'][0]['text'] = 'changed'
    assert profile.dimensions[0].questions[0].text != 'changed'
    assert EvaluationProfile.from_json(profile.to_json()) == profile


def test_baseline_matches_legacy_catalogue():
    from devops_flow.calms import load_questionnaire
    profile = baseline_profile()
    assert len(profile.question_ids) == 15
    assert profile.question_ids == tuple(q.lower() for q in load_questionnaire().question_ids)
    assert len(profile.rules) == 6
    assert (profile.risk_threshold, profile.min_coverage) == (50, 2/3)


def test_versioning_current_and_section_diff(store):
    profile = EvaluationProfile.from_dict(profile_data())
    first = store.publish_profile(profile)
    data = first.to_dict()
    data['calms']['risk_threshold'] = 60
    data['rules'].pop()
    updated = EvaluationProfile.from_dict(data)
    assert changed_sections(first, updated) == ('Seuils', 'Règles')
    second = store.publish_profile(updated, expected_version=1)
    assert second.version == 2
    assert store.get_profile(first.slug).version == 2
    assert store.get_profile(first.slug,1).to_json() == first.to_json()
    assert not store.get_profile(first.slug,1).is_current
    with pytest.raises(ValueError, match='version courante'):
        store.publish_profile(updated, expected_version=1)


def test_soft_delete_and_reserved_baseline(store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    store.archive_profile(profile.slug)
    assert not store.get_profile(profile.slug).is_active
    assert [p.slug for p in store.list_profiles()] == ['baseline']
    assert len(store.list_profiles(include_archived=True)) == 2
    with pytest.raises(ValueError):
        store.publish_profile(profile, expected_version=1)
    with pytest.raises(ValueError):
        store.publish_profile(baseline_profile())
    with pytest.raises(ValueError):
        store.archive_profile('baseline')
