"""Stockage SQLite local ; toutes les connexions sont fermées après usage."""

from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from devops_assist.calms import CalmsDraft
from devops_assist.models import Project
from devops_assist.diagnostic import DiagnosticSnapshot
from devops_assist.roadmap import Action, validate_followup, validate_position


@dataclass(frozen=True)
class StoredDiagnostic:
    id: str
    project_id: str
    created_at: datetime
    snapshot: DiagnosticSnapshot


@dataclass(frozen=True)
class DiagnosticSummary:
    id: str
    project_id: str
    created_at: datetime


class ProjectStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL
                        CHECK (length(trim(name)) BETWEEN 1 AND 120),
                    description TEXT NOT NULL DEFAULT ''
                        CHECK (length(description) <= 2000),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    draft_answers_json TEXT
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(projects)")}
            if "draft_answers_json" not in columns:
                connection.execute("ALTER TABLE projects ADD COLUMN draft_answers_json TEXT")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS diagnostics (
                    id TEXT PRIMARY KEY NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    submission_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    questionnaire_version TEXT,
                    rules_version TEXT,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(id, project_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY NOT NULL,
                    project_id TEXT NOT NULL,
                    diagnostic_id TEXT NOT NULL,
                    recommendation_id TEXT NOT NULL,
                    source_finding_ids_json TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority REAL NOT NULL,
                    position INTEGER NOT NULL CHECK(position >= 0),
                    status TEXT NOT NULL DEFAULT 'todo'
                        CHECK(status IN ('todo', 'in_progress', 'done', 'dismissed')),
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(diagnostic_id, recommendation_id),
                    FOREIGN KEY(diagnostic_id, project_id) REFERENCES diagnostics(id, project_id)
                )
            """)

    def save_diagnostic(self, project_id: str, submission_key: str,
                        snapshot: DiagnosticSnapshot) -> StoredDiagnostic:
        if not isinstance(submission_key, str) or not submission_key.strip() or len(submission_key) > 120:
            raise ValueError("La clé de soumission est invalide.")
        # Valider le format, sans relancer le moteur ni recalculer le diagnostic.
        snapshot = DiagnosticSnapshot.from_json(snapshot.to_json())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM diagnostics WHERE submission_key = ?", (submission_key,)).fetchone()
            if existing is not None:
                if existing["project_id"] != project_id:
                    raise ValueError("Cette soumission appartient à un autre projet.")
                return self._diagnostic(existing)
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ValueError("Le projet demandé n'est plus disponible.")
            return self._insert_diagnostic(connection, project_id, submission_key, snapshot)

    def _insert_diagnostic(self, connection: sqlite3.Connection, project_id: str,
                           submission_key: str, snapshot: DiagnosticSnapshot) -> StoredDiagnostic:
        data = snapshot.to_dict()
        diagnostic_id, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        connection.execute("""
            INSERT INTO diagnostics VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (diagnostic_id, project_id, submission_key, now, data["schema_version"],
              data["versions"]["questionnaire"], data["versions"]["rules"], snapshot.to_json()))
        for position, recommendation in enumerate(data["recommendations"]):
            connection.execute("""
                INSERT INTO actions (id, project_id, diagnostic_id, recommendation_id,
                    source_finding_ids_json, title, description, priority, position, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(uuid4()), project_id, diagnostic_id, recommendation["id"],
                  json.dumps(recommendation["source_finding_ids"]), recommendation["title"],
                  recommendation["description"], recommendation["priority"], position, now, now))
        row = connection.execute("SELECT * FROM diagnostics WHERE id = ?", (diagnostic_id,)).fetchone()
        return self._diagnostic(row)

    def create_project_with_diagnostic(self, name: str, description: str, answers: Mapping,
                                       snapshot: DiagnosticSnapshot) -> tuple[Project, StoredDiagnostic]:
        """Insertion seule, atomique : un échec ne laisse pas de démo partielle."""
        project = Project.new(name, description)
        draft = CalmsDraft.new(answers)
        snapshot = DiagnosticSnapshot.from_json(snapshot.to_json())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
                (project.id, project.name, project.description, project.created_at.isoformat(),
                 project.updated_at.isoformat(), draft.to_json()),
            )
            diagnostic = self._insert_diagnostic(connection, project.id, str(uuid4()), snapshot)
        return project, diagnostic

    def get_diagnostic(self, project_id: str, diagnostic_id: str) -> StoredDiagnostic | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM diagnostics WHERE project_id = ? AND id = ?",
                                     (project_id, diagnostic_id)).fetchone()
        return self._diagnostic(row) if row else None

    def latest_diagnostic(self, project_id: str) -> StoredDiagnostic | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM diagnostics WHERE project_id = ? ORDER BY created_at DESC, id ASC LIMIT 1",
                                     (project_id,)).fetchone()
        return self._diagnostic(row) if row else None

    def list_diagnostics(self, project_id: str) -> list[DiagnosticSummary]:
        # Ne pas charger tous les snapshots : une ancienne version illisible
        # ne doit pas empêcher de choisir un autre diagnostic.
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, project_id, created_at FROM diagnostics WHERE project_id = ? "
                "ORDER BY created_at DESC, id ASC", (project_id,),
            ).fetchall()
        return [DiagnosticSummary(r["id"], r["project_id"], datetime.fromisoformat(r["created_at"])) for r in rows]

    def diagnostic_state(self, project_id: str, diagnostic_id: str) -> tuple[StoredDiagnostic, tuple[Action, ...]]:
        """Lire snapshot et suivi dans la même transaction, notamment pour l'export."""
        with self._connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM diagnostics WHERE project_id = ? AND id = ?",
                                     (project_id, diagnostic_id)).fetchone()
            if row is None:
                raise ValueError("Le diagnostic demandé n'est pas disponible pour ce projet.")
            rows = connection.execute(
                "SELECT * FROM actions WHERE project_id = ? AND diagnostic_id = ? ORDER BY position, id",
                (project_id, diagnostic_id),
            ).fetchall()
            return self._diagnostic(row), tuple(self._action(r) for r in rows)

    def update_action(self, project_id: str, diagnostic_id: str, action_id: str,
                      *, status: str, notes: str) -> None:
        validate_followup(status, notes)
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE actions SET status = ?, notes = ?, updated_at = ? "
                "WHERE project_id = ? AND diagnostic_id = ? AND id = ? AND (status != ? OR notes != ?)",
                (status, notes, datetime.now(timezone.utc).isoformat(), project_id, diagnostic_id, action_id, status, notes),
            )
            if result.rowcount == 0 and connection.execute(
                "SELECT 1 FROM actions WHERE project_id = ? AND diagnostic_id = ? AND id = ?",
                (project_id, diagnostic_id, action_id),
            ).fetchone() is None:
                raise ValueError("L'action demandée n'est pas disponible pour ce diagnostic.")

    def move_action(self, project_id: str, diagnostic_id: str, action_id: str, position: int) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, position FROM actions WHERE project_id = ? AND diagnostic_id = ? ORDER BY position, id",
                (project_id, diagnostic_id),
            ).fetchall()
            ids = [r["id"] for r in rows]
            if action_id not in ids:
                raise ValueError("L'action demandée n'est pas disponible pour ce diagnostic.")
            validate_position(position, len(ids))
            ids.remove(action_id)
            ids.insert(position, action_id)
            old_positions = {r["id"]: r["position"] for r in rows}
            now = datetime.now(timezone.utc).isoformat()
            for index, id in enumerate(ids):
                if old_positions[id] != index:
                    connection.execute(
                        "UPDATE actions SET position = ?, updated_at = ? WHERE id = ? AND project_id = ? AND diagnostic_id = ?",
                        (index, now, id, project_id, diagnostic_id),
                    )

    @staticmethod
    def _action(row: sqlite3.Row) -> Action:
        return Action(row["id"], row["project_id"], row["diagnostic_id"], row["recommendation_id"],
                      row["position"], row["status"], row["notes"], datetime.fromisoformat(row["created_at"]),
                      datetime.fromisoformat(row["updated_at"]))

    @staticmethod
    def _diagnostic(row: sqlite3.Row) -> StoredDiagnostic:
        return StoredDiagnostic(row["id"], row["project_id"], datetime.fromisoformat(row["created_at"]),
                                DiagnosticSnapshot.from_json(row["snapshot_json"]))

    def create_project(self, name: str, description: str = "") -> Project:
        project = Project.new(name, description)
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO projects (id, name, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    project.id,
                    project.name,
                    project.description,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )
        return project

    def list_projects(self) -> list[Project]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY created_at DESC, id ASC"
            ).fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, project_id: str) -> Project | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._project(row) if row else None

    def save_calms(self, project_id: str, answers: Mapping, notes: Mapping | None = None) -> CalmsDraft:
        draft = CalmsDraft.new(answers, notes)
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE projects SET draft_answers_json = ?, updated_at = ? WHERE id = ?",
                (draft.to_json(), draft.saved_at.isoformat(), project_id),
            )
            if result.rowcount != 1:
                raise ValueError("Le projet demandé n'est plus disponible.")
        return draft

    def load_calms(self, project_id: str) -> CalmsDraft | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT draft_answers_json FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Le projet demandé n'est plus disponible.")
        return CalmsDraft.from_json(row["draft_answers_json"]) if row["draft_answers_json"] is not None else None

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
