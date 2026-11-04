"""Projection pure multi-composants : toutes les sources sont conservées."""
from dataclasses import replace
import json


def build_project_flow(snapshot):
    from devops_flow.diagnostic import DiagnosticSnapshot
    from devops_flow.diagram import FlowDiagram, FlowNode, FlowEdge, FlowAnnotation, aggregate_status, build_flow, STAGES
    data = snapshot.to_dict()
    calms = [f for f in data['findings'] if f['category'] == 'CALMS']
    scopes = sorted({f['scope_id'] for f in data['findings'] if f['category'] != 'CALMS'})
    nodes_by_id, component_annotations, culture = {}, {}, None
    enabled = [r['id'] for r in data['profile']['rules']] if data.get('profile') else ['DOC-001', 'DOC-002', 'DOC-003', 'CMP-001', 'CI-001', 'CI-002']
    for scope in scopes or ['project']:
        own = [f for f in data['findings'] if f['category'] != 'CALMS' and f['scope_id'] == scope]
        own_ids = {f['id'] for f in own}
        by_rule = {f['rule_id']: f for f in own}
        placeholders = [dict(id='scope_unavailable', rule_id=rule, status='not_applicable', source='inconnu',
            explanation='Contrôle hors de ce périmètre.', limitation='Aucune attribution implicite.', evidence=[])
            for rule in enabled if rule not in by_rule]
        legacy = dict(data, schema_version='diagnostic-v2' if data.get('profile') else 'diagnostic-v1', findings=calms + own + placeholders)
        flow = build_flow(DiagnosticSnapshot(json.dumps(legacy, ensure_ascii=False)))
        culture = flow.culture
        annotations = []
        for node in flow.nodes:
            valid = tuple(a for a in node.annotations if a.finding_id != 'scope_unavailable')
            annotations.extend(a for a in valid if a.finding_id in own_ids)
            if node.id not in nodes_by_id:
                nodes_by_id[node.id] = replace(node, annotations=())
            current = nodes_by_id[node.id]
            merged = tuple(dict.fromkeys((*current.annotations, *valid)))
            nodes_by_id[node.id] = replace(current, annotations=merged, status=aggregate_status(a.status for a in merged))
        component_annotations[scope] = tuple(dict.fromkeys(annotations))
    nodes = list(nodes_by_id.values())
    edges = [FlowEdge(STAGES[i][0], STAGES[i + 1][0]) for i in range(7)] + [FlowEdge('feedback', 'code', 'feedback')]
    for node in nodes:
        if node.kind == 'service':
            edges.append(FlowEdge('operate', node.id, 'service'))
    components = data['architecture']['components']
    if len(components) > 1:
        for component in components:
            annotations = component_annotations.get(component['id'], ())
            if not annotations and not enabled:
                profile = data['profile']
                annotations = (FlowAnnotation('not_applicable', 'inconnu',
                    'Tous les contrôles techniques sont désactivés dans le profil figé.',
                    profile_ref=f"{profile['slug']}:{profile['version']}"),)
            nodes.append(FlowNode(component['id'], component['path'] if component['path'] != '.' else '(racine)',
                aggregate_status(a.status for a in annotations), annotations, kind='component'))
            edges.append(FlowEdge(component['id'], 'build', 'scope'))
        for relation in data['architecture']['relations']:
            if relation['kind'] == 'compose_dependency':
                edges.append(FlowEdge(relation['source'], relation['target'], 'declaration'))
    return FlowDiagram(tuple(nodes), tuple(dict.fromkeys(edges)), culture, version='diagram-v2')
