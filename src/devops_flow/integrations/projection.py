"""Entrée de projection indépendante du fournisseur ; aucun accès réseau."""
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Callable, Protocol, TypeVar

STATUSES = {'todo': 'À faire', 'in_progress': 'En cours', 'done': 'Terminé', 'dismissed': 'Écarté'}
CHECKLIST_NAME = 'Critères de vérification'
DEFAULT_CRITERIA = ("Preuve d'intégration fournie", 'Revue effectuée')


@dataclass(frozen=True)
class PlannedCard:
    action_id: str
    status: str
    name: str
    description: str
    criteria: tuple[str, ...]


@dataclass(frozen=True)
class ActionPlanPreview:
    project_id: str
    diagnostic_id: str
    board_name: str
    mode: str
    cards: tuple[PlannedCard, ...]
    fingerprint: str
    projection_id: str | None = None

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value):
        data = json.loads(value)
        data['cards'] = tuple(PlannedCard(**{**c, 'criteria': tuple(c['criteria'])}) for c in data['cards'])
        return cls(**data)


Result = TypeVar('Result', covariant=True)


class ProjectionProvider(Protocol[Result]):
    def preview(self, project_id: str, diagnostic_id: str, mode: str = 'create') -> ActionPlanPreview: ...
    def project(self, preview: ActionPlanPreview,
                progress: Callable[[str], None] | None = None) -> Result: ...


def build_preview(project, diagnostic, actions, *, mode, projection_id=None, projected=(), catalog=None):
    data = diagnostic.snapshot.to_dict()
    recs = {r['id']: r for r in data['recommendations']}
    cards = []
    for action in actions:
        rec = recs.get(action.recommendation_id)
        if rec is None or action.status not in STATUSES:
            raise ValueError('Une action est incompatible avec le diagnostic sélectionné.')
        if mode == 'append' and action.id in projected:
            continue
        criteria = DEFAULT_CRITERIA
        if catalog and catalog.get('version') == rec.get('template_version', data['versions']['recommendations']):
            rule = rec.get('rule_id') or rec['id'].removeprefix('REC-').rsplit('-', 1)[0]
            provided = catalog.get('rules', {}).get(rule, {}).get('verification_criteria')
            if (isinstance(provided, (list, tuple)) and provided
                    and all(isinstance(x, str) and x.strip() and len(x) <= 2000 for x in provided)):
                criteria = tuple(dict.fromkeys(x.strip() for x in provided))
        desc = (f"{rec['description']}\n\nPriorité : {rec['priority']:g} · Impact : {rec['impact']}"
                f" · Effort : {rec['effort']}\n{rec['justification']}\n\n"
                f"Diagnostic : {diagnostic.id}\nAction : {action.id}\n"
                f"Recommandation : {rec['id']}\nConstats : {', '.join(rec['source_finding_ids'])}")
        cards.append(PlannedCard(action.id, action.status, rec['title'], desc, criteria))
    payload = {'project': project.id, 'name': project.name, 'diagnostic': diagnostic.id,
               'snapshot': data, 'actions': [(a.id, a.status, a.position) for a in actions],
               'projected': sorted(projected), 'mode': mode, 'projection': projection_id,
               'cards': [asdict(c) for c in cards]}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return ActionPlanPreview(project.id, diagnostic.id, project.name + ' — devops-flow',
                             mode, tuple(cards), fingerprint, projection_id)
