"""Questionnaire, validation et scores CALMS ; aucun import GUI ou base."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
import json

UNKNOWN = "unknown"
MAX_NOTE_LENGTH = 2000


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    help: str = ""


@dataclass(frozen=True)
class Dimension:
    id: str
    label: str
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class Questionnaire:
    version: str
    dimensions: tuple[Dimension, ...]

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(q.id for d in self.dimensions for q in d.questions)


@lru_cache(maxsize=1)
def load_questionnaire() -> Questionnaire:
    data = json.loads(
        files("devops_flow").joinpath("data/calms_questions.json").read_text(encoding="utf-8")
    )
    return Questionnaire(
        version=data["version"],
        dimensions=tuple(
            Dimension(d["id"], d["label"], tuple(Question(**q) for q in d["questions"]))
            for d in data["dimensions"]
        ),
    )


def normalize_answers(answers: Mapping, *, profile=None) -> dict[str, int | str]:
    ids = profile.question_ids if profile else load_questionnaire().question_ids
    if not isinstance(answers, Mapping) or any(key not in ids for key in answers):
        raise ValueError("Les réponses CALMS contiennent un format ou une question non reconnu.")
    normalized = {}
    for question_id in ids:
        value = answers.get(question_id, UNKNOWN)
        if not ((type(value) is int and 0 <= value <= 3) or
                (type(value) is str and value == UNKNOWN)):
            raise ValueError("Chaque réponse doit être un entier de 0 à 3 ou « inconnu ».")
        normalized[question_id] = value
    return normalized


def normalize_notes(notes: Mapping, *, profile=None) -> dict[str, str]:
    ids = profile.question_ids if profile else load_questionnaire().question_ids
    if not isinstance(notes, Mapping) or any(key not in ids for key in notes):
        raise ValueError("Les notes CALMS contiennent un format ou une question non reconnu.")
    normalized = {}
    for question_id, note in notes.items():
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH:
            raise ValueError("Chaque note doit être un texte de 2 000 caractères maximum.")
        if note.strip():
            normalized[question_id] = note.strip()
    return normalized


@dataclass(frozen=True)
class DimensionScore:
    id: str
    label: str
    score: float | None
    answered: int
    total: int
    min_coverage: float = 2 / 3
    risk_threshold: float = 50

    @property
    def coverage(self) -> float:
        return self.answered / self.total

    @property
    def interpretation(self) -> str:
        if self.answered == 0:
            return "Aucune réponse connue : cette dimension reste indéterminée."
        if self.coverage < self.min_coverage:
            return "Couverture insuffisante pour interpréter cette dimension ; complétez les réponses."
        if self.score < self.risk_threshold:
            return "Les réponses déclarent des pratiques peu systématisées ; cet indicateur interne reste à confirmer."
        return "Les réponses déclarent des pratiques partiellement ou régulièrement établies ; elles ne constituent pas une certification."


def calculate_scores(answers: Mapping, *, profile=None) -> tuple[DimensionScore, ...]:
    normalized = normalize_answers(answers, profile=profile)
    scores = []
    for dimension in (profile.dimensions if profile else load_questionnaire().dimensions):
        known = [normalized[q.id] for q in dimension.questions if normalized[q.id] != UNKNOWN]
        scores.append(DimensionScore(
            dimension.id, dimension.label,
            sum(known) / (3 * len(known)) * 100 if known else None,
            len(known), len(dimension.questions),
            profile.min_coverage if profile else 2 / 3,
            profile.risk_threshold if profile else 50,
        ))
    return tuple(scores)


@dataclass(frozen=True)
class CalmsDraft:
    version: str
    answers: dict[str, int | str]
    notes: dict[str, str]
    saved_at: datetime

    @classmethod
    def new(cls, answers: Mapping, notes: Mapping | None = None, *, profile=None) -> "CalmsDraft":
        return cls(
            profile.questionnaire.version if profile else load_questionnaire().version,
            normalize_answers(answers, profile=profile),
            normalize_notes({} if notes is None else notes, profile=profile), datetime.now(timezone.utc),
        )

    def to_json(self) -> str:
        return json.dumps({
            "version": self.version, "answers": self.answers, "notes": self.notes,
            "saved_at": self.saved_at.isoformat(),
        }, ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_json(cls, raw: str, *, profile=None) -> "CalmsDraft":
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"version", "answers", "notes", "saved_at"}:
                raise ValueError
            if data["version"] != (profile.questionnaire.version if profile else load_questionnaire().version):
                raise ValueError
            saved_at = datetime.fromisoformat(data["saved_at"])
            if saved_at.utcoffset() is None:
                raise ValueError
            return cls(data["version"], normalize_answers(data["answers"], profile=profile),
                       normalize_notes(data["notes"], profile=profile), saved_at)
        except (ValueError, TypeError, KeyError):
            raise ValueError(
                "Le questionnaire enregistré est illisible ou sa version n'est pas prise en charge."
            ) from None
