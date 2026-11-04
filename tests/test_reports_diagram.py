from dataclasses import replace
import json

import pytest

from devops_flow.diagnostic import build_diagnostic
from devops_flow.diagram import build_flow, snapshot_to_mermaid
from devops_flow.profiles import baseline_profile
from devops_flow.reports import ReportExport, build_export, render_json, render_markdown


@pytest.mark.parametrize('profile', [None, baseline_profile(), replace(baseline_profile(), slug='custom', rules=())])
def test_export_projects_same_flow_and_keeps_original_snapshot(store, profile):
    if profile and profile.slug != 'baseline':
        profile = store.publish_profile(profile)
    project = store.create_project('PRIVATE_NAME', profile_slug=profile.slug if profile else 'baseline')
    snapshot = build_diagnostic(answers={}, profile=profile)
    saved = store.save_diagnostic(project.id, 'one', snapshot)
    diagnostic, actions = store.diagnostic_state(project.id, saved.id)
    report = build_export(diagnostic, actions)
    data = report.to_dict()
    assert data['report_version'] == 'report-v3'
    assert data['diagram']['mermaid'] == snapshot_to_mermaid(snapshot)
    assert {k: v for k, v in data['diagram'].items() if k != 'mermaid'} == build_flow(snapshot).to_dict()
    assert '```mermaid\n' + data['diagram']['mermaid'] + '```' in render_markdown(report)
    assert 'F-CAL-C' in render_markdown(report)
    assert 'PRIVATE_NAME' not in render_json(report) + render_markdown(report)
    assert store.get_diagnostic(project.id, saved.id).snapshot == snapshot


@pytest.mark.parametrize('version', ['report-v1', 'report-v2'])
def test_old_report_objects_still_render_without_diagram(store, version):
    project = store.create_project('Old')
    saved = store.save_diagnostic(project.id, 'old', build_diagnostic(answers={}, profile=baseline_profile() if version == 'report-v2' else None))
    diagnostic, actions = store.diagnostic_state(project.id, saved.id)
    data = build_export(diagnostic, actions).to_dict()
    data.pop('diagram')
    data['report_version'] = version
    report = ReportExport(json.dumps(data))
    assert json.loads(render_json(report)) == data
    assert version in render_markdown(report)
    assert '```mermaid' not in render_markdown(report)
