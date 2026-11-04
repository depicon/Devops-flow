"""Catalogue de recommandations et classement déterministe ; aucune dépendance GUI/SQL."""

from dataclasses import dataclass
from importlib.resources import files
import json

from devops_flow.models import Finding


def load_catalog() -> dict:
    # Objet neuf à chaque appel : les consommateurs ne peuvent altérer le catalogue.
    return json.loads(files("devops_flow").joinpath("data/recommendations.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Recommendation:
    id: str
    source_finding_ids: tuple[str, ...]
    kind: str
    title: str
    description: str
    severity: str | None
    impact: int
    effort: int
    priority: float
    justification: str


def recommend(findings: tuple[Finding, ...], *, profile=None) -> tuple[Recommendation, ...]:
    catalog = load_catalog()["rules"]
    grouped = {}
    for finding in findings:
        if profile and finding.rule_id in {"DOC-001", "DOC-002", "DOC-003", "CMP-001", "CI-001", "CI-002"} and finding.rule_id not in profile.rule_ids:
            continue
        if finding.status in {"observed", "not_applicable"}:
            continue
        entry = catalog[finding.rule_id]
        kind = "risk" if finding.status == "risk" else "verify"
        recommendation_id = f"REC-{finding.rule_id}-{kind.upper()}"
        grouped.setdefault(recommendation_id, (entry, kind, set()))[2].add(finding.id)
    results = []
    for recommendation_id, (entry, kind, ids) in grouped.items():
        if kind == "risk":
            template = entry["risk"]
            title, description = template["title"], template["description"]
            impact, effort, severity = template["impact"], template["effort"], template["severity"]
        else:
            title = entry["verify"]
            description = "À confirmer avec l'équipe et les configurations pertinentes ; l'information disponible ne justifie pas un correctif certain."
            impact, effort, severity = 1, 1, None
        if profile:
            override = next((r for r in profile.rules if recommendation_id.startswith(f'REC-{r.id}-')), None)
            if override:
                impact = override.impact if override.impact is not None else impact
                effort = override.effort if override.effort is not None else effort
        justification = (
            "Signal technique explicite : vérifier le contexte avant correction."
            if kind == "risk" and severity else
            ("Pratiques déclarées peu systématisées, avec couverture minimale du profil atteinte." if profile else
             "Pratiques déclarées peu systématisées, avec au moins deux réponses connues.")
            if kind == "risk" else
            "Information manquante ou non analysable : vérification, sans gravité technique établie."
        )
        results.append(Recommendation(
            recommendation_id, tuple(sorted(ids)), kind, title, description, severity,
            impact, effort, impact / effort, justification,
        ))
    return tuple(sorted(results, key=lambda r: (-r.priority, r.id)))
