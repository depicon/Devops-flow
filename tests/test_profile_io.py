import pytest
from devops_flow.profiles import EvaluationProfile, MAX_PROFILE_BYTES
from devops_flow.profile_io import parse_markdown, export_markdown
from test_profiles import profile_data


@pytest.mark.parametrize('body', ['', 'Texte français éà\n', '\n## Titre\n\n- test\n', '```yaml\na: b\n```'])
def test_markdown_round_trip(body):
    data = profile_data()
    data['conventions'] = body
    profile = EvaluationProfile.from_dict(data)
    exported = export_markdown(profile)
    assert parse_markdown(exported).profile == profile
    assert export_markdown(parse_markdown(exported).profile) == exported
    assert parse_markdown(exported).question_counts == dict.fromkeys('CALMS',3)


@pytest.mark.parametrize('content,message', [
    (b'\xff', 'UTF-8'), (b'x'*(MAX_PROFILE_BYTES+1),'256 Kio'),
    (b'name: test','commencer'), (b'---\nname: test','manquante'),
    (b'---\na: 1\na: 2\n---\n', 'YAML invalide'),
    (b'---\na: &x [1]\nb: *x\n---\n','YAML invalide'),
    (b'---\na: !!python/object:danger {}\n---\n','YAML invalide'),
    (b'---\na: !include secret\n---\n','YAML invalide'),
    (b'---\n{1: bad}\n---\n','YAML invalide'),
    (b'---\nconventions: bad\n---\n','corps Markdown'),
    (b'---\nslug: ok\n---\n<!DOCTYPE x>', 'entit'),
    (b'---\nslug: ok\n---\n&#60;', 'entit'),
    (b'---\na: '+ b'['*34 + b'1'+b']'*34+b'\n---\n','YAML invalide'),
])
def test_import_rejects_unsafe_or_invalid_files_without_echo(content, message):
    with pytest.raises(ValueError, match=message) as error:
        parse_markdown(content)
    assert 'secret' not in str(error.value)


def test_invalid_schema_and_unknown_fields_rejected():
    raw = export_markdown(EvaluationProfile.from_dict(profile_data()))
    with pytest.raises(ValueError, match='champs attendus'):
        parse_markdown(raw.replace(b'slug:', b'unknown:', 1))
