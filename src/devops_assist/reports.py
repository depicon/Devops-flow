"""Objet d'export contrôlé unique, puis rendus Markdown/JSON sans accès au dépôt."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from devops_assist.db import StoredDiagnostic
from devops_assist.roadmap import Action, STATUS_LABELS, validate_followup

REPORT_VERSION = "report-v1"
# Contrat diagnostic-v1, indépendant des futurs catalogues et sans import du scanner.
QUESTION_IDS = tuple(f"{d}{n}" for d in "CALMS" for n in (1, 2, 3))
CONFIG_NAMES = frozenset({"Dockerfile", "compose.yaml", "compose.yml", "docker-compose.yaml",
                          "docker-compose.yml", ".gitlab-ci.yml"})
MARKDOWN_LABELS = {
    "engine": "Moteur", "questionnaire": "Questionnaire", "rules": "Règles",
    "scanner": "Scanner", "recommendations": "Recommandations", "calms": "Réponses CALMS",
    "scan": "Scan", "source": "Origine", "files": "Configurations ciblées",
    "id": "Identifiant", "label": "Dimension", "score": "Score sur 100",
    "answered": "Réponses connues", "total": "Questions", "coverage": "Couverture (0 à 1)",
    "rule_id": "Règle", "category": "Catégorie", "status": "Statut", "explanation": "Explication",
    "limitation": "Limite", "severity": "Gravité", "recommendation_ids": "Recommandations liées",
    "evidence": "Preuves minimales", "code": "Code de preuve", "question_id": "Question",
    "value": "Réponse", "file": "Configuration", "line": "Ligne", "kind": "Type",
    "title": "Action", "description": "Description", "justification": "Justification",
    "impact": "Impact", "effort": "Effort", "priority": "Priorité initiale (impact / effort)",
    "source_finding_ids": "Constats liés", "recommendation_id": "Recommandation liée",
    "position": "Position (à partir de 0)", "created_at": "Création", "updated_at": "Mise à jour",
}
MARKDOWN_STATUSES = {
    **STATUS_LABELS, "observed": "Indice observé", "not_observed": "Non observé",
    "unknown": "À confirmer", "invalid": "Non analysable", "risk": "Point à vérifier",
    "not_applicable": "Non applicable",
}


def _select(item: dict, names: str) -> dict:
    return {key: item[key] for key in names.split()}


@dataclass(frozen=True)
class ReportExport:
    """Une chaîne figée évite que les deux formats voient deux états différents."""
    _json: str

    def to_dict(self) -> dict:
        return json.loads(self._json)


def build_export(diagnostic: StoredDiagnostic, actions: tuple[Action, ...],
                 *, exported_at: datetime | None = None) -> ReportExport:
    data = diagnostic.snapshot.to_dict()
    answers = data["inputs"]["calms"]
    scan = data["inputs"]["scan"]
    try:
        if answers is not None:
            # Ne jamais copier un dictionnaire de contexte ou de saisies libres.
            answers = {q: answers[q] for q in QUESTION_IDS}
            if any(not (type(v) is int and 0 <= v <= 3) and v != "unknown" for v in answers.values()):
                raise ValueError
        if scan is not None:
            if scan["source"] not in {"local", "zip"} or any(f not in CONFIG_NAMES for f in scan["files"]):
                raise ValueError
            scan = {"source": scan["source"], "files": list(scan["files"])}
        findings = []
        for item in data["findings"]:
            finding = _select(item, "id rule_id category status source explanation limitation severity recommendation_ids")
            finding["evidence"] = []
            for evidence in item["evidence"]:
                if evidence["file"] is not None and evidence["file"] not in CONFIG_NAMES:
                    raise ValueError
                if evidence["question_id"] is not None and evidence["question_id"] not in QUESTION_IDS:
                    raise ValueError
                finding["evidence"].append(_select(evidence, "code question_id value file line"))
            findings.append(finding)
        recommendations = [_select(r, "id kind title description justification impact effort priority severity source_finding_ids")
                           for r in data["recommendations"]]
        rec_ids = {r["id"] for r in recommendations}
        ordered = sorted(actions, key=lambda a: (a.position, a.id))
        if (len(ordered) != len(rec_ids) or {a.recommendation_id for a in ordered} != rec_ids
                or [a.position for a in ordered] != list(range(len(ordered)))):
            raise ValueError
        followup = []
        for action in ordered:
            if action.project_id != diagnostic.project_id or action.diagnostic_id != diagnostic.id:
                raise ValueError
            validate_followup(action.status, action.notes)
            followup.append({
                "id": action.id, "recommendation_id": action.recommendation_id,
                "position": action.position, "status": action.status,
                "created_at": action.created_at.isoformat(), "updated_at": action.updated_at.isoformat(),
            })
        now = exported_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        report = {
            "report_version": REPORT_VERSION,
            "project_id": diagnostic.project_id, "diagnostic_id": diagnostic.id,
            "diagnostic_created_at": diagnostic.created_at.isoformat(),
            "exported_at": now.astimezone(timezone.utc).isoformat(),
            "schema_version": data["schema_version"],
            "versions": _select(data["versions"], "engine questionnaire rules scanner recommendations"),
            "inputs": {"calms": answers, "scan": scan},
            "scores": [_select(s, "id label score answered total coverage") for s in data["scores"]],
            "findings": findings,
            "alignments": [_select(a, "id question_id source_finding_ids explanation limitation") for a in data["alignments"]],
            "recommendations": recommendations, "actions": followup,
            "limitations": list(data["limitations"]),
            "export_policy": "État des actions à la date d'export ; noms et descriptions de projet, notes, commentaires, chemins et contenus bruts exclus.",
        }
        return ReportExport(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Le rapport ne peut pas être préparé : données enregistrées incompatibles.") from None


def render_json(report: ReportExport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"


def render_markdown(report: ReportExport) -> str:
    data = report.to_dict()
    lines = ["# Rapport DevOps Assist", "", f"Projet : `{data['project_id']}`",
             f"Diagnostic : `{data['diagnostic_id']}`", f"Créé le : {data['diagnostic_created_at']}",
             f"État des actions exporté le : {data['exported_at']}", "", data["export_policy"], "",
             f"Format : {data['report_version']} · Snapshot : {data['schema_version']}", ""]

    def scalar(value):
        if value is None:
            return "null (indéterminé ou non applicable)"
        # Les textes proviennent du snapshot contrôlé, jamais des saisies locales.
        return str(value).replace("\n", " ")

    def tree(value, depth=0):
        prefix = "  " * depth
        if isinstance(value, dict):
            for key, item in value.items():
                label = MARKDOWN_LABELS.get(key, key)
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}- {label} :" + (" aucun" if not item else ""))
                    tree(item, depth + 1)
                else:
                    displayed = MARKDOWN_STATUSES.get(item, item) if key == "status" else item
                    lines.append(f"{prefix}- {label} : {scalar(displayed)}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{prefix}- Élément :")
                    tree(item, depth + 1)
                else:
                    lines.append(f"{prefix}- {scalar(item)}")
        else:
            lines.append(f"{prefix}- {scalar(value)}")

    for title, key in (("Versions", "versions"), ("Sources et réponses", "inputs"),
                       ("Scores CALMS et couverture", "scores"), ("Constats et preuves", "findings"),
                       ("Rapprochements à confirmer", "alignments"), ("Recommandations initiales", "recommendations"),
                       ("Suivi des actions dans l'ordre choisi", "actions"), ("Limites", "limitations")):
        lines.extend([f"## {title}", ""])
        tree(data[key])
        if not data[key]:
            lines.append("Aucun élément.")
        lines.append("")
    return "\n".join(lines)
