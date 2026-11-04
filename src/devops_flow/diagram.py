"""Projection vérifiable d'un résultat figé ; ni scanner, ni UI, ni catalogue courant."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from html import escape
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from devops_flow.models import FindingEvidence

if TYPE_CHECKING:
    from devops_flow.diagnostic import DiagnosticSnapshot

DIAGRAM_VERSION = 'diagram-v1'
STATUS_LABELS = MappingProxyType({
    'observed': 'Observé', 'risk': 'Point à vérifier', 'not_observed': 'Non observé',
    'unknown': 'À confirmer', 'invalid': 'Non analysable', 'not_applicable': 'Non applicable',
})
# Tokens purs, réexposés par ui.theme : les exports n'importent jamais l'UI.
STATUS_COLORS = MappingProxyType({
    'observed': ('#166534', '#FFFFFF', '#14532D'),
    'risk': ('#B91C1C', '#FFFFFF', '#991B1B'),
    'not_observed': ('#92400E', '#FFFFFF', '#78350F'),
    'unknown': ('#475569', '#FFFFFF', '#334155'),
    'invalid': ('#6D28D9', '#FFFFFF', '#5B21B6'),
    'not_applicable': ('#E5E7EB', '#374151', '#9CA3AF'),
})
DIMENSIONS = MappingProxyType({'C': 'Culture', 'A': 'Automation', 'L': 'Lean',
                              'M': 'Measurement', 'S': 'Sharing'})
STAGES = (
    ('code', 'Code', ('L1', 'L2', 'S1'), (), ('L', 'S')),
    ('build', 'Construction', (), ('DOC-001', 'CI-001', 'CI-002'), ('A',)),
    ('test', 'Tests', ('A2',), ('CI-002',), ('A', 'L')),
    ('package', 'Paquet', (), ('DOC-002', 'DOC-001'), ()),
    ('deploy', 'Déploiement', ('A3',), ('CI-002',), ('A', 'L')),
    ('operate', 'Exploitation', (), ('DOC-003', 'CMP-001'), ()),
    ('observe', 'Observation', ('M1', 'M2', 'M3'), (), ('M',)),
    ('feedback', 'Retours', ('S2', 'S3', 'C3'), (), ('S',)),
)
SERVICE_TYPES = ('postgres', 'redis', 'mysql', 'rabbitmq', 'cache', 'database', 'queue', 'unresolved')
CONFIG_NAMES = frozenset({'Dockerfile', '.gitlab-ci.yml', 'compose.yaml', 'compose.yml',
                          'docker-compose.yaml', 'docker-compose.yml'})
PROOF_CODES = frozenset({'file_found', 'explicit_latest', 'image_variable', 'no_explicit_latest',
                         'final_user', 'no_final_user', 'docker_unknown', 'docker_invalid',
                         'privileged_true', 'no_privileged_true', 'compose_unresolved', 'invalid_compose',
                         'local_test_job', 'test_job_unconfirmed', 'invalid_ci',
                         'local_build_job', 'build_job_unconfirmed', 'local_deploy_job', 'deploy_job_unconfirmed',
                         *('service_type_' + kind for kind in SERVICE_TYPES)})


@dataclass(frozen=True)
class FlowAnnotation:
    status: str
    source: str
    reason: str
    finding_id: str | None = None
    rule_id: str | None = None
    question_id: str | None = None
    profile_ref: str | None = None
    evidence: tuple[FindingEvidence, ...] = ()
    limitation: str = ''


@dataclass(frozen=True)
class FlowNode:
    id: str
    label: str
    status: str
    annotations: tuple[FlowAnnotation, ...]
    dimensions: tuple[str, ...] = ()
    kind: str = 'stage'


@dataclass(frozen=True)
class FlowEdge:
    source: str
    target: str
    kind: str = 'sequence'


@dataclass(frozen=True)
class FlowDiagram:
    nodes: tuple[FlowNode, ...]
    edges: tuple[FlowEdge, ...]
    culture: FlowAnnotation
    version: str = DIAGRAM_VERSION

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), ensure_ascii=False, allow_nan=False))


def aggregate_status(statuses) -> str:
    values = tuple(statuses)
    if any(s not in STATUS_LABELS for s in values):
        raise ValueError('Statut de flux inconnu.')
    if values and all(s == 'not_applicable' for s in values):
        return 'not_applicable'
    return next((s for s in ('risk', 'invalid', 'unknown', 'not_observed', 'observed') if s in values), 'unknown')


def _proofs(finding, codes=None):
    from devops_flow.architecture import relative_path
    def allowed(path):
        if path in CONFIG_NAMES:
            return True
        try:
            return 'scope_id' in finding and isinstance(path, str) and relative_path(path).rsplit('/', 1)[-1] in CONFIG_NAMES
        except ValueError:
            return False
    return tuple(FindingEvidence(code=e['code'], file=e['file'], line=e['line'])
                 for e in finding['evidence'] if e['code'] in PROOF_CODES
                 and allowed(e['file']) and (codes is None or e['code'] in codes))


def build_flow(snapshot: DiagnosticSnapshot) -> FlowDiagram:
    data = snapshot.to_dict()
    if data.get('schema_version') == 'diagnostic-v3':
        from devops_flow.project_flow import build_project_flow
        return build_project_flow(snapshot)
    profile = data.get('profile')
    ref = f"{profile['slug']}:{profile['version']}" if profile else None
    baseline = profile is None or profile['slug'] == 'baseline'
    threshold = profile['calms']['risk_threshold'] if profile else 50
    by_rule = {f['rule_id']: f for f in data['findings']}
    answers = data['inputs']['calms']

    def question(qid):
        finding = by_rule['CAL-' + qid[0]]
        if answers is None:
            return FlowAnnotation('not_applicable', 'inconnu', 'Questionnaire passé pour ce diagnostic.',
                                  finding['id'], finding['rule_id'])
        if not baseline:
            return FlowAnnotation('unknown', 'inconnu', 'Correspondance des questions avec cette étape non définie.',
                                  profile_ref=ref, limitation='Les IDs personnalisés ne garantissent pas le sens des questions.')
        key = qid.lower() if profile else qid
        if key not in answers:
            return FlowAnnotation('unknown', 'inconnu', 'Aucune donnée pour cette question.',
                                  finding['id'], finding['rule_id'], profile_ref=ref)
        value = answers[key]
        status = 'unknown' if value == 'unknown' else 'risk' if value / 3 * 100 < threshold else 'observed'
        return FlowAnnotation(status, 'déclaré', f'Réponse {key} évaluée au seuil {threshold:g} / 100.',
                              finding['id'], finding['rule_id'], key,
                              evidence=(FindingEvidence('answer', question_id=key, value=value),),
                              limitation='Signal déclaratif par réponse, distinct du score et du constat de dimension ; sans certification.')

    def technical(rule, stage):
        finding = by_rule.get(rule)
        if finding is None:
            # Les formats v1 contiennent toutes les règles ; v2 omet les désactivées.
            if ref is None:
                raise ValueError('Source du flux indisponible.')
            return FlowAnnotation('not_applicable', 'inconnu', f'{rule} désactivée dans le profil figé.',
                                  rule_id=rule, profile_ref=ref)
        status = finding['status']
        proofs = _proofs(finding)
        reason = 'Statut du constat technique.'
        if rule == 'CI-002' and stage == 'test':
            proofs = _proofs(finding, {'local_test_job', 'test_job_unconfirmed', 'invalid_ci'})
        if rule == 'CI-002' and stage in {'build', 'deploy'}:
            codes = {'local_' + stage + '_job', stage + '_job_unconfirmed', 'invalid_ci'}
            proofs = _proofs(finding, codes)
            if status != 'not_applicable':
                status = 'observed' if any(e.code == 'local_' + stage + '_job' for e in proofs) else (
                    'invalid' if status == 'invalid' else 'unknown')
            reason = ('Indice local de job de ' + ('construction' if stage == 'build' else 'déploiement') +
                      ' ; exécution à confirmer.' if status == 'observed' else 'Signal spécifique à cette étape à confirmer.')
            if status == 'not_applicable':
                reason = 'Configuration CI non fournie ou scan passé.'
        return FlowAnnotation(status, 'observé' if status in {'observed', 'risk'} else 'inconnu', reason,
                              finding['id'], rule, evidence=proofs, limitation=finding['limitation'])

    nodes = []
    for stage, label, questions, rules, dimensions in STAGES:
        annotations = tuple(dict.fromkeys([*(question(q) for q in questions),
                                           *(technical(r, stage) for r in rules)]))
        nodes.append(FlowNode(stage, label, aggregate_status(a.status for a in annotations), annotations, dimensions))
    edges = [FlowEdge(STAGES[i][0], STAGES[i + 1][0]) for i in range(7)]
    edges.append(FlowEdge('feedback', 'code', 'feedback'))
    compose = by_rule.get('CMP-001')
    if compose and compose['status'] != 'not_applicable':
        for kind in SERVICE_TYPES:
            proofs = _proofs(compose, {'service_type_' + kind})
            if not proofs:
                continue
            status = 'unknown' if kind == 'unresolved' else 'observed'
            annotation = FlowAnnotation(status, 'observé', 'Déclaration de service dans Compose.',
                                        compose['id'], 'CMP-001', evidence=proofs,
                                        limitation='Type de configuration uniquement ; service actif et dépendance externe non établis.')
            node_id = 'service_' + kind
            nodes.append(FlowNode(node_id, 'Service externe (type non résolu)' if kind == 'unresolved' else kind,
                                  status, (annotation,), kind='service'))
            edges.append(FlowEdge('operate', node_id, 'service'))
    culture = by_rule['CAL-C']
    return FlowDiagram(tuple(nodes), tuple(edges), FlowAnnotation(
        culture['status'], culture['source'], 'Culture : dimension transverse du diagnostic.',
        culture['id'], 'CAL-C', limitation=culture['limitation']))


def render_mermaid(flow: FlowDiagram, *, highlighted_status: str | None = None) -> str:
    """Seulement des IDs contrôlés et des libellés échappés ; aucun callback ni URL."""
    if highlighted_status is not None and highlighted_status not in STATUS_LABELS:
        raise ValueError('Statut de légende inconnu.')
    ids = {node.id for node in flow.nodes}
    if len(ids) != len(flow.nodes) or any(not re.fullmatch(r'[a-z][a-z0-9_]*', key) for key in ids):
        raise ValueError('Identifiant de nœud invalide.')
    lines = ['flowchart LR']
    for status, (fill, color, stroke) in STATUS_COLORS.items():
        dash = ',stroke-dasharray:5 5' if status == 'not_applicable' else ''
        lines.append(f'    classDef {status} fill:{fill},color:{color},stroke:{stroke}{dash}')
    lines += ['    classDef selected stroke:#0F172A,stroke-width:5px',
              '    subgraph culture["Culture · transverse"]', '        direction LR']
    services = []
    for node in flow.nodes:
        if node.status not in STATUS_LABELS:
            raise ValueError('Statut de flux inconnu.')
        references = list(dict.fromkeys(a.question_id or a.rule_id or 'Profil figé' for a in node.annotations))
        label = [escape(node.label, quote=True), escape(' · '.join(references), quote=True),
                 STATUS_LABELS[node.status]]
        if node.dimensions:
            label.append('CALMS : ' + ' '.join(f'[{d}]' for d in node.dimensions))
        declaration = f'{node.id}["' + '<br/>'.join(label) + '"]'
        if node.kind == 'service':
            services.append('    ' + declaration)
        else:
            lines.append('        ' + declaration)
    lines += ['    end', '    style culture fill:#F8FAFC,stroke:#2563EB,stroke-width:2px,color:#0F172A']
    lines.extend(services)
    for edge in flow.edges:
        if edge.source not in ids or edge.target not in ids:
            raise ValueError('Arête de flux invalide.')
        lines.append(f'    {edge.source} ' + ('-->' if edge.kind == 'sequence' else '-.->') + f' {edge.target}')
    for node in flow.nodes:
        lines.append(f'    class {node.id} {node.status}')
    selected = [n.id for n in flow.nodes if n.status == highlighted_status]
    if selected:
        lines.append('    class ' + ','.join(selected) + ' selected')
    return '\n'.join(lines) + '\n'


def snapshot_to_mermaid(snapshot: DiagnosticSnapshot) -> str:
    return render_mermaid(build_flow(snapshot))
