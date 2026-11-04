from dataclasses import replace
import json
import pytest
from devops_flow.profiles import EvaluationProfile
from devops_flow.diagnostic import build_diagnostic
from devops_flow.reports import build_export, render_markdown, render_json
from devops_flow.ui.markdown import sanitize_html
from test_profiles import profile_data


def test_report_includes_frozen_conventions_and_excludes_private_fields(store):
    data = profile_data()
    data.update(author='SENTINEL_AUTHOR',description='SENTINEL_DESCRIPTION')
    data['calms']['questions']['C'][0]['help'] = 'SENTINEL_HELP'
    p = store.publish_profile(EvaluationProfile.from_dict(data))
    project = store.create_project('SENTINEL_PROJECT','SENTINEL_CONTEXT',profile_slug=p.slug)
    draft = store.save_calms(project.id,{}, {'c1':'SENTINEL_NOTE'},profile=p)
    stored = store.save_diagnostic(project.id,'s',build_diagnostic(answers=draft.answers,profile=p))
    actions = store.diagnostic_state(project.id,stored.id)[1]
    store.update_action(project.id,stored.id,actions[0].id,status='done',notes='SENTINEL_COMMENT')
    store.publish_profile(replace(p,conventions='Nouvelles conventions'),expected_version=1)
    stored, actions = store.diagnostic_state(project.id,stored.id)
    report = build_export(stored,actions)
    raw, markdown = render_json(report), render_markdown(report)
    assert report.to_dict()['report_version'] == 'report-v3'
    assert report.to_dict()['profile']['conventions'] == p.conventions
    assert p.conventions in markdown
    assert 'Nouvelles conventions' not in raw
    assert 'SENTINEL' not in raw+markdown
    assert set(json.loads(raw)['inputs']['calms']) == set(p.question_ids)


@pytest.mark.parametrize('html', [
    '<img src="https://example.invalid/x" onerror="evil()">',
    '<script>evil()</script>', '<svg><a href="x">evil</a></svg>',
    '<iframe src="https://example.invalid"></iframe>',
    '<a href="javascript:evil()" onclick="evil()">Lien</a>',
    '<a href="https://example.invalid">Lien</a>',
    '<p style="background:url(https://example.invalid)">Texte</p>',
])
def test_sanitizer_removes_active_html_and_remote_resources(html):
    cleaned = sanitize_html(html)
    assert all(marker not in cleaned for marker in ('src=','onerror','onclick','javascript:','https://','<svg','<script','<iframe','style='))


def test_markdown_whitelist_preserves_structure_and_local_anchors():
    assert sanitize_html('<h2>Titre</h2><p><strong>Texte</strong> <a href="#titre">aller</a></p>') == '<h2>Titre</h2><p><strong>Texte</strong> <a href="#titre">aller</a></p>'
