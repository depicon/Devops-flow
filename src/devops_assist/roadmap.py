"""Suivi des actions : valeurs validées, sans dépendance à la GUI."""

from dataclasses import dataclass
from datetime import datetime

STATUS_LABELS = {
    "todo": "À faire", "in_progress": "En cours",
    "done": "Terminée", "dismissed": "Écartée",
}
MAX_COMMENT_LENGTH = 2000


@dataclass(frozen=True)
class Action:
    id: str
    project_id: str
    diagnostic_id: str
    recommendation_id: str
    position: int
    status: str
    notes: str
    created_at: datetime
    updated_at: datetime


def validate_followup(status: str, notes: str) -> None:
    if not isinstance(status, str) or status not in STATUS_LABELS:
        raise ValueError("Le statut de l'action est invalide.")
    if not isinstance(notes, str) or len(notes) > MAX_COMMENT_LENGTH:
        raise ValueError("Le commentaire doit contenir au maximum 2 000 caractères.")


def validate_position(position: int, count: int) -> None:
    if type(position) is not int or not 0 <= position < count:
        raise ValueError("La position de l'action est invalide.")
