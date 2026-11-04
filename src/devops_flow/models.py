"""Objets et validation métier, sans dépendance graphique."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

MAX_PROJECT_NAME_LENGTH = 120
MAX_PROJECT_DESCRIPTION_LENGTH = 2000

FINDING_STATUSES = ("observed", "risk", "not_observed", "unknown", "not_applicable", "invalid")
TECHNICAL_RULE_IDS = ("DOC-001", "DOC-002", "DOC-003", "CMP-001", "CI-001", "CI-002")


@dataclass(frozen=True)
class FindingEvidence:
    code: str
    question_id: str | None = None
    value: int | str | None = None
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class Finding:
    id: str
    rule_id: str
    category: str
    status: str
    source: str
    explanation: str
    evidence: tuple[FindingEvidence, ...]
    limitation: str
    severity: str | None = None
    recommendation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    profile_ref: str = "baseline:1"

    @property
    def profile_slug(self):
        return self.profile_ref.rsplit(':', 1)[0]

    @property
    def profile_version(self):
        return int(self.profile_ref.rsplit(':', 1)[1])

    @classmethod
    def new(cls, name: str, description: str = "") -> "Project":
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Le nom du projet est obligatoire.")
        name = name.strip()
        if len(name) > MAX_PROJECT_NAME_LENGTH:
            raise ValueError("Le nom du projet est limité à 120 caractères.")
        if not isinstance(description, str):
            raise ValueError("La description doit être un texte.")
        description = description.strip()
        if len(description) > MAX_PROJECT_DESCRIPTION_LENGTH:
            raise ValueError("La description est limitée à 2 000 caractères.")
        now = datetime.now(timezone.utc)
        return cls(str(uuid4()), name, description, now, now)
