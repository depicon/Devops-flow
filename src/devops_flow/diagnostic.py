"""Assemblage pur et snapshot figé ; jamais de scan ou recalcul lors de la lecture."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import json
import math
from typing import TYPE_CHECKING

from devops_flow.calms import calculate_scores, load_questionnaire, normalize_answers
from devops_flow.models import FINDING_STATUSES, Finding, FindingEvidence, TECHNICAL_RULE_IDS
from devops_flow.recommendations import Recommendation, load_catalog, recommend
if TYPE_CHECKING:
    from devops_flow.scanner import ScanResult

SCHEMA_VERSION = "diagnostic-v1"
ENGINE_VERSION = "diagnostic-engine-v1"
LIMITATION = "Interprétation déclarative interne ; seuil 50 et couverture 2/3 proposés, sans certification."


def _validate_snapshot(data: dict) -> None:
    """Vérifier la structure sauvegardée, indépendamment des catalogues courants."""
    if isinstance(data, dict) and data.get('schema_version') == 'diagnostic-v3':
        from devops_flow.project_diagnostic import validate_project_snapshot
        return validate_project_snapshot(data)
    def require(condition):
        if not condition:
            raise ValueError

    require(isinstance(data, dict) and data.get("schema_version") in {SCHEMA_VERSION, 'diagnostic-v2'})
    profile = None
    if data['schema_version'] == 'diagnostic-v2':
        from devops_flow.profiles import EvaluationProfile
        profile = EvaluationProfile.from_dict(data['profile'])
    for key in ("findings", "recommendations", "scores", "alignments", "limitations"):
        require(isinstance(data.get(key), list))
    require(isinstance(data.get("versions"), dict)
            and set(data["versions"]) == {"engine", "questionnaire", "rules", "scanner", "recommendations"}
            and all(v is None or isinstance(v, str) for v in data["versions"].values()))
    require(isinstance(data.get("inputs"), dict) and set(data["inputs"]) == {"calms", "scan"})
    rule_ids = {f"CAL-{d}" for d in "CALMS"} | set(profile.rule_ids if profile else TECHNICAL_RULE_IDS)
    require(len(data["findings"]) == len(rule_ids) and len(data["recommendations"]) <= len(rule_ids))
    if profile:
        answers = data['inputs']['calms']
        require(answers is None or normalize_answers(answers, profile=profile) == answers)
    for finding in data["findings"]:
        require(isinstance(finding, dict) and set(finding) == {f.name for f in fields(Finding)}
                and finding.get("rule_id") in rule_ids)
        require(finding.get("id") == f"F-{finding['rule_id']}" and finding.get("status") in FINDING_STATUSES)
        require(finding.get("source") in {"déclaré", "observé", "inconnu"})
        require(all(isinstance(finding.get(k), str) for k in ("category", "explanation", "limitation")))
        require(isinstance(finding.get("evidence"), list) and isinstance(finding.get("recommendation_ids"), list))
        require(finding.get("severity") in {None, "élevée", "modérée"})
        for evidence in finding["evidence"]:
            require(isinstance(evidence, dict) and set(evidence) == {"code", "question_id", "value", "file", "line"})
            require(isinstance(evidence["code"], str))
            require(all(evidence[k] is None or isinstance(evidence[k], str) for k in ("question_id", "file")))
            require(evidence["line"] is None or (type(evidence["line"]) is int and evidence["line"] > 0))
            require(evidence["value"] is None or evidence["value"] == "unknown"
                    or (type(evidence["value"]) is int and 0 <= evidence["value"] <= 3))
    require({f["rule_id"] for f in data["findings"]} == rule_ids)
    ids = {f["id"] for f in data["findings"]}
    for item in data["recommendations"]:
        require(isinstance(item, dict) and set(item) == {f.name for f in fields(Recommendation)})
        require(all(isinstance(item.get(k), str) for k in ("id", "kind", "title", "description", "justification")))
        require(isinstance(item.get("source_finding_ids"), list) and bool(item["source_finding_ids"])
                and all(id in ids for id in item["source_finding_ids"]))
        require(type(item.get("impact")) is int and 1 <= item["impact"] <= 3)
        require(type(item.get("effort")) is int and 1 <= item["effort"] <= 3)
        require(type(item.get("priority")) in (int, float) and math.isfinite(item["priority"]))
        require(item.get("severity") in {None, "élevée", "modérée"})
    rec_ids = {r["id"] for r in data["recommendations"]}
    require(len(rec_ids) == len(data["recommendations"]))
    require(all(all(id in rec_ids for id in f["recommendation_ids"]) for f in data["findings"]))
    for score in data["scores"]:
        require(isinstance(score, dict) and set(score) == {"id", "label", "score", "answered", "total", "coverage"}
                and all(isinstance(score.get(k), str) for k in ("id", "label")))
        require(all(type(score.get(k)) is int for k in ("answered", "total")) and score["total"] > 0)
        require(type(score.get("coverage")) in (int, float) and 0 <= score["coverage"] <= 1)
        require(score.get("score") is None or (type(score["score"]) in (int, float) and 0 <= score["score"] <= 100))
    for item in data["alignments"]:
        require(isinstance(item, dict) and all(isinstance(item.get(k), str) for k in ("id", "question_id", "explanation", "limitation")))
        require(isinstance(item.get("source_finding_ids"), list) and all(id in ids for id in item["source_finding_ids"]))
    require(all(isinstance(item, str) for item in data["limitations"]))
    if profile:
        require({s['id'] for s in data['scores']} == (set('CALMS') if data['inputs']['calms'] is not None else set()))
        for score in data['scores']:
            dimension = next(d for d in profile.dimensions if d.id == score['id'])
            require(score['total'] == len(dimension.questions) and 0 <= score['answered'] <= score['total'])
            require(score['coverage'] == score['answered'] / score['total'])
        for finding in data['findings']:
            for evidence in finding['evidence']:
                require(evidence['question_id'] is None or evidence['question_id'] in profile.question_ids)
        for alignment in data['alignments']:
            require(alignment['question_id'] in profile.question_ids)


def calms_findings(answers: dict | None, *, profile=None) -> tuple[Finding, ...]:
    scores = calculate_scores(answers, profile=profile) if answers is not None else None
    normalized = normalize_answers(answers, profile=profile) if answers is not None else None
    findings = []
    for index, dimension in enumerate(profile.dimensions if profile else load_questionnaire().dimensions):
        rule_id = f"CAL-{dimension.id}"
        if scores is None:
            status, explanation, evidence = "not_applicable", "Questionnaire passé pour ce diagnostic.", ()
        else:
            score = scores[index]
            status = "unknown" if score.answered == 0 or score.coverage < score.min_coverage else "risk" if score.score < score.risk_threshold else "observed"
            explanation = score.interpretation
            evidence = tuple(FindingEvidence("answer", question_id=q.id, value=normalized[q.id]) for q in dimension.questions)
        findings.append(Finding(
            f"F-{rule_id}", rule_id, "CALMS", status,
            "déclaré" if scores is not None else "inconnu", explanation, evidence,
            (f'Interprétation déclarative interne ; seuil {profile.risk_threshold:g}, couverture {profile.min_coverage:g}, sans certification.' if profile else LIMITATION),
        ))
    return tuple(findings)


@dataclass(frozen=True)
class DiagnosticSnapshot:
    _json: str

    def to_json(self) -> str:
        return self._json

    def to_dict(self) -> dict:
        return json.loads(self._json)

    @classmethod
    def from_json(cls, raw: str) -> "DiagnosticSnapshot":
        try:
            data = json.loads(raw)
            _validate_snapshot(data)
            # La lecture conserve les versions enregistrées sans appeler le moteur courant.
            return cls(raw)
        except (ValueError, TypeError, KeyError, StopIteration, RecursionError):
            raise ValueError("Le diagnostic enregistré est illisible ou sa version n'est pas prise en charge.") from None


def build_diagnostic(*, answers: dict | None = None, scan: ScanResult | None = None, profile=None) -> DiagnosticSnapshot:
    from devops_flow.project_scanner import ScanResultV2
    if isinstance(scan, ScanResultV2):
        from devops_flow.project_diagnostic import build_project_diagnostic
        return build_project_diagnostic(answers=answers, scan=scan, profile=profile)
    if profile is not None:
        from devops_flow.profiles import EvaluationProfile
        if not isinstance(profile, EvaluationProfile) or not profile.is_active:
            raise ValueError('Choisissez un profil actif connu.')
        EvaluationProfile.from_dict(profile.to_dict())
    if answers is None and scan is None:
        raise ValueError("Enregistrez un questionnaire ou réalisez un scan admissible avant de finaliser.")
    if scan is not None:
        from devops_flow.scanner import ScanResult
        if not isinstance(scan, ScanResult):
            raise ValueError("Un scan rejeté ou incomplet ne constitue pas une entrée de diagnostic.")
    normalized = normalize_answers(answers, profile=profile) if answers is not None else None
    findings = list(calms_findings(normalized, profile=profile))
    if scan is None:
        findings.extend(Finding(f"F-{rule}", rule, "Technique", "not_applicable", "inconnu",
                                "Scan passé pour ce diagnostic.", (), "Aucune conclusion technique sans scan.")
                        for rule in (profile.rule_ids if profile else TECHNICAL_RULE_IDS))
    else:
        if tuple(f.rule_id for f in scan.findings) != TECHNICAL_RULE_IDS:
            raise ValueError("Le résultat du scan est incomplet ou non pris en charge.")
        from devops_flow.rules import select_findings
        findings.extend(Finding(
            f"F-{f.rule_id}", f.rule_id, "Technique", f.status, f.source, f.explanation,
            tuple(FindingEvidence(e.code, file=e.file, line=e.line) for e in f.evidence), f.limitation,
        ) for f in select_findings(scan.findings, profile=profile))
    recommendations = recommend(tuple(findings), profile=profile)
    findings = [replace(
        f, recommendation_ids=tuple(r.id for r in recommendations if f.id in r.source_finding_ids),
        severity=next((r.severity for r in recommendations if f.id in r.source_finding_ids), None),
    ) for f in findings]
    by_rule = {f.rule_id: f for f in findings}
    alignments = []
    if normalized is not None and scan is not None and (profile is None or profile.slug == 'baseline'):
        for question, rule, practice in (("A2", "CI-002", "tests systématisés"), ("A1", "CI-001", "build systématisé")):
            if profile:
                question = question.lower()
            if normalized[question] == 3 and rule in by_rule and by_rule[rule].status in {"not_observed", "unknown", "invalid", "not_applicable"}:
                alignments.append({
                    "id": f"ALIGN-{question}-{rule}", "source_finding_ids": ["F-CAL-A", f"F-{rule}"],
                    "question_id": question,
                    "explanation": f"Déclaration et indices à rapprocher : {practice} déclaré, indice technique à confirmer dans ce périmètre.",
                    "limitation": "Aucune contradiction démontrée, aucun malus CALMS ; une autre CI ou un autre emplacement reste possible.",
                })
    scores = [] if normalized is None else [dict(id=s.id, label=s.label, score=s.score, answered=s.answered,
                                                total=s.total, coverage=s.coverage)
                                          for s in calculate_scores(normalized, profile=profile)]
    data = {
        "schema_version": 'diagnostic-v2' if profile else SCHEMA_VERSION,
        "versions": {"engine": 'diagnostic-engine-v2' if profile else ENGINE_VERSION,
                     "questionnaire": (profile.questionnaire.version if profile else load_questionnaire().version) if normalized is not None else None,
                     "rules": scan.rules_version if scan is not None else None,
                     "scanner": scan.scanner_version if scan is not None else None,
                     "recommendations": load_catalog()["version"]},
        "inputs": {"calms": normalized, "scan": None if scan is None else {"source": scan.source, "files": list(scan.files)}},
        "scores": scores, "findings": [asdict(f) for f in findings],
        "alignments": alignments, "recommendations": [asdict(r) for r in recommendations],
        "limitations": ["Déclarations et indices statiques à confirmer ; aucun pipeline ni code exécuté.",
                        "Priorité initiale = impact / effort décroissant, puis identifiant croissant ; aucune dépendance automatique.",
                        "GitLab CI provisoire ; includes, héritage et conditions non résolus. Aucun score global."],
    }
    if profile:
        data['profile'] = profile.to_dict()
    return DiagnosticSnapshot(json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True))
