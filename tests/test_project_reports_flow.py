from dataclasses import replace
import json

from devops_flow.diagnostic import build_diagnostic
from devops_flow.diagram import build_flow, render_mermaid
from devops_flow.reports import build_export, render_json, render_markdown
from devops_flow.integrations.projection import build_preview
from tests.test_project_diagnostic import project_scan


def test_architecture_reports_and_flow_preserve_all_sources(store):
    project = store.create_project('PRIVATE_PROJECT')
    scan = project_scan()
    snapshot = build_diagnostic(scan=scan, profile=store.project_profile(project.id))
    stored = store.save_diagnostic(project.id, 'architecture', snapshot)
    _, actions = store.diagnostic_state(project.id, stored.id)
    exported = build_export(stored, actions)
    data = exported.to_dict()
    assert data['report_version'] == 'report-v4'
    assert data['diagram']['version'] == 'diagram-v2'
    assert data['architecture'] == json.loads(json.dumps(snapshot.to_dict()['architecture']))
    assert 'backend/Dockerfile' in render_json(exported)
    assert 'Architecture DevOps' in render_markdown(exported)
    assert 'PRIVATE_PROJECT' not in render_json(exported)
    flow = build_flow(snapshot)
    components = [n for n in flow.nodes if n.kind == 'component']
    assert len(components) == 2
    build = next(n for n in flow.nodes if n.id == 'build')
    ids = {a.finding_id for a in build.annotations}
    expected = {f['id'] for f in snapshot.to_dict()['findings'] if f['rule_id'] == 'DOC-001'}
    assert expected <= ids
    assert render_mermaid(flow) == render_mermaid(build_flow(snapshot))
    valid = {f['id'] for f in snapshot.to_dict()['findings']}
    assert all(a.finding_id is None or a.finding_id in valid for n in flow.nodes for a in n.annotations)


def test_followup_is_independent_and_trello_never_receives_paths(store):
    p = store.create_project('A')
    s = store.save_diagnostic(p.id, 's', build_diagnostic(scan=project_scan(), profile=store.project_profile(p.id)))
    _, actions = store.diagnostic_state(p.id, s.id)
    siblings = [a for a in actions if a.recommendation_id.endswith('DOC-002-RISK')]
    store.update_action(p.id, s.id, siblings[0].id, status='done', notes='PRIVATE_NOTE')
    stored, actions = store.diagnostic_state(p.id, s.id)
    assert next(a.status for a in actions if a.id == siblings[1].id) == 'todo'
    preview = build_preview(p, stored, actions, mode='create')
    outbound = repr(preview.cards)
    assert 'backend' not in outbound and 'frontend' not in outbound and 'PRIVATE_NOTE' not in outbound
    assert all('/' not in c.name for c in preview.cards)


def test_profile_without_rules_still_has_valid_flow(store):
    p = store.create_project('A')
    profile = replace(store.project_profile(p.id), rules=())
    flow = build_flow(build_diagnostic(scan=project_scan(), profile=profile))
    assert flow.version == 'diagram-v2'
    assert len([n for n in flow.nodes if n.kind == 'component']) == 2
    assert all(a.finding_id != 'scope_unavailable' for n in flow.nodes for a in n.annotations)
    assert all(n.status == 'not_applicable' and n.annotations[0].profile_ref
               for n in flow.nodes if n.kind == 'component')
