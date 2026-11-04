"""Stockage SQLite local ; toutes les connexions sont fermées après usage."""

from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from devops_flow.calms import CalmsDraft
from devops_flow.models import Project
from devops_flow.diagnostic import DiagnosticSnapshot
from devops_flow.roadmap import Action, validate_followup, validate_position
from devops_flow.profiles import EvaluationProfile, baseline_profile
from devops_flow.project_content import ProjectContentStore
from devops_flow.integrations.storage import TrelloStore
from devops_flow.component_overrides import ArchitectureStore


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
    profile_ref: str = 'baseline:1'


class ProjectStore(ProjectContentStore, TrelloStore, ArchitectureStore):
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
            self._initialize_profiles(connection)
            self._initialize_content(connection)
            self._initialize_trello(connection)
            self._initialize_architecture(connection)

    def save_diagnostic(self, project_id: str, submission_key: str,
                        snapshot: DiagnosticSnapshot, *, expected_draft=None, remote_acquisition_id=None) -> StoredDiagnostic:
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
            profile = self._profile_for_project(connection, project_id)
            data = snapshot.to_dict()
            if data['schema_version'] == 'diagnostic-v3' and data['inputs']['scan']['override_revision'] != self._override_revision(connection, project_id):
                raise ValueError('Les déclarations de stack ont changé. Relancez l’analyse avant de créer le diagnostic.')
            if data.get('profile') is not None:
                embedded = EvaluationProfile.from_dict(data['profile'])
                if embedded.to_json() != profile.to_json():
                    raise ValueError('Le profil du projet a changé pendant la finalisation. Rechargez le diagnostic.')
            elif profile.slug != 'baseline':
                raise ValueError('Le diagnostic doit utiliser le profil imposé au projet.')
            if not profile.is_active:
                raise ValueError('Le profil du projet est archivé.')
            if expected_draft is not None:
                row = connection.execute('SELECT draft_json FROM calms_drafts WHERE project_id=? AND profile_ref=?',
                                         (project_id, f'{profile.slug}:{profile.version}')).fetchone()
                if row is None or json.loads(row['draft_json'])['saved_at'] != expected_draft:
                    raise ValueError('Le questionnaire a changé pendant la finalisation. Rechargez le diagnostic.')
            stored = self._insert_diagnostic(connection, project_id, submission_key, snapshot)
            if remote_acquisition_id is not None:
                connection.execute('INSERT INTO diagnostic_remote_sources VALUES (?,?,?)',
                                   (stored.id, project_id, remote_acquisition_id))
            return stored

    def _insert_diagnostic(self, connection: sqlite3.Connection, project_id: str,
                           submission_key: str, snapshot: DiagnosticSnapshot) -> StoredDiagnostic:
        data = snapshot.to_dict()
        diagnostic_id, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        connection.execute("""
            INSERT INTO diagnostics (id, project_id, submission_key, created_at, schema_version,
                questionnaire_version, rules_version, snapshot_json, profile_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (diagnostic_id, project_id, submission_key, now, data["schema_version"],
              data["versions"]["questionnaire"], data["versions"]["rules"], snapshot.to_json(),
              f"{data['profile']['slug']}:{data['profile']['version']}" if data.get('profile') else 'baseline:1'))
        profile = data.get('profile')
        connection.execute('UPDATE diagnostics SET profile_ref=? WHERE id=?',
                           (f"{profile['slug']}:{profile['version']}" if profile else 'baseline:1', diagnostic_id))
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
        embedded = snapshot.to_dict().get('profile')
        profile = EvaluationProfile.from_dict(embedded) if embedded else None
        if profile is not None and profile.to_json() != baseline_profile().to_json():
            raise ValueError('La démo doit utiliser le référentiel de base.')
        draft = CalmsDraft.new(answers, profile=profile)
        snapshot = DiagnosticSnapshot.from_json(snapshot.to_json())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO projects (id,name,description,created_at,updated_at,draft_answers_json,profile_ref) VALUES (?, ?, ?, ?, ?, ?, 'baseline:1')",
                (project.id, project.name, project.description, project.created_at.isoformat(),
                 project.updated_at.isoformat(), draft.to_json()),
            )
            diagnostic = self._insert_diagnostic(connection, project.id, str(uuid4()), snapshot)
            canonical = self._convert_draft(draft, baseline_profile())
            connection.execute('INSERT INTO calms_drafts VALUES (?,?,?)', (project.id, 'baseline:1', canonical.to_json()))
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
                "SELECT id, project_id, created_at, profile_ref FROM diagnostics WHERE project_id = ? "
                "ORDER BY created_at DESC, id ASC", (project_id,),
            ).fetchall()
        return [DiagnosticSummary(r["id"], r["project_id"], datetime.fromisoformat(r["created_at"]), r['profile_ref']) for r in rows]

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

    def create_project(self, name: str, description: str = "", *, profile_slug='baseline', profile_version=None) -> Project:
        project = Project.new(name, description)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            profile = self._get_profile(connection, profile_slug, profile_version)
            if profile is None or not profile.is_active:
                raise ValueError('Choisissez un profil actif connu.')
            project = replace(project, profile_ref=f'{profile.slug}:{profile.version}')
            connection.execute(
                """INSERT INTO projects (id, name, description, created_at, updated_at, profile_ref)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project.id,
                    project.name,
                    project.description,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.profile_ref,
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

    def save_calms(self, project_id: str, answers: Mapping, notes: Mapping | None = None, *, profile=None,
                   expected_saved_at=None) -> CalmsDraft:
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            assigned = self._profile_for_project(connection, project_id)
            if profile is not None and profile.to_json() != assigned.to_json():
                raise ValueError('Le profil du projet a changé. Rechargez le questionnaire.')
            if not assigned.is_active:
                raise ValueError('Le profil du projet est archivé.')
            legacy = profile is None and assigned.slug == 'baseline'
            draft = CalmsDraft.new(answers, notes, profile=None if legacy else assigned)
            canonical = self._convert_draft(draft, assigned) if legacy else draft
            ref = f'{assigned.slug}:{assigned.version}'
            if expected_saved_at is not None:
                row = connection.execute('SELECT draft_json FROM calms_drafts WHERE project_id=? AND profile_ref=?',
                                         (project_id, ref)).fetchone()
                current = json.loads(row['draft_json'])['saved_at'] if row else ''
                if current != expected_saved_at:
                    raise ValueError('Le questionnaire a été modifié dans une autre session. Rechargez avant de réessayer.')
            connection.execute('INSERT INTO calms_drafts VALUES (?,?,?) ON CONFLICT(project_id,profile_ref) DO UPDATE SET draft_json=excluded.draft_json',
                               (project_id, ref, canonical.to_json()))
            result = connection.execute(
                "UPDATE projects SET draft_answers_json = ?, updated_at = ? WHERE id = ?",
                (draft.to_json(), draft.saved_at.isoformat(), project_id),
            )
            if result.rowcount != 1:
                raise ValueError("Le projet demandé n'est plus disponible.")
        return draft

    def load_calms(self, project_id: str, *, profile=None) -> CalmsDraft | None:
        with self._connection() as connection:
            assigned = self._profile_for_project(connection, project_id)
            if profile is not None and profile.to_json() != assigned.to_json():
                raise ValueError('Le profil du projet a changé. Rechargez le questionnaire.')
            row = connection.execute(
                'SELECT draft_json FROM calms_drafts WHERE project_id=? AND profile_ref=?',
                (project_id, f'{assigned.slug}:{assigned.version}'),
            ).fetchone()
        if row is None:
            return None
        draft = CalmsDraft.from_json(row['draft_json'], profile=assigned)
        if profile is None and assigned.slug == 'baseline':
            return CalmsDraft('calms-v1', {k.upper(): v for k,v in draft.answers.items()},
                              {k.upper(): v for k,v in draft.notes.items()}, draft.saved_at)
        return draft

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            profile_ref=row['profile_ref'],
        )

    @staticmethod
    def _convert_draft(draft, profile):
        """Adapter uniquement les identifiants du catalogue historique baseline."""
        raw = json.loads(draft.to_json())
        raw['version'] = profile.questionnaire.version
        raw['answers'] = {k.lower(): v for k, v in raw['answers'].items()}
        raw['notes'] = {k.lower(): v for k, v in raw['notes'].items()}
        return CalmsDraft.from_json(json.dumps(raw), profile=profile)

    def _initialize_profiles(self, connection):
        connection.execute('''CREATE TABLE IF NOT EXISTS profiles (
            slug TEXT PRIMARY KEY NOT NULL, is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS profile_versions (
            ref TEXT PRIMARY KEY NOT NULL,
            slug TEXT NOT NULL REFERENCES profiles(slug) ON DELETE RESTRICT,
            version INTEGER NOT NULL CHECK(version >= 1),
            definition_json TEXT NOT NULL,
            is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
            UNIQUE(slug,version), CHECK(ref = slug || ':' || version))''')
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_current_profile ON profile_versions(slug) WHERE is_current=1')
        base = baseline_profile()
        connection.execute("INSERT OR IGNORE INTO profiles VALUES ('baseline',1)")
        connection.execute("INSERT OR IGNORE INTO profile_versions VALUES ('baseline:1','baseline',1,?,1)", (base.to_json(),))
        for table in ('projects', 'diagnostics'):
            columns = {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')}
            if 'profile_ref' not in columns:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN profile_ref TEXT REFERENCES profile_versions(ref) ON DELETE RESTRICT')
                connection.execute(f"UPDATE {table} SET profile_ref='baseline:1'")
            for operation in ('INSERT', 'UPDATE'):
                connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_profile_required_{operation.lower()}
                    BEFORE {operation} ON {table} WHEN NEW.profile_ref IS NULL
                    BEGIN SELECT RAISE(ABORT, 'profile required'); END''')
        migrate_drafts = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calms_drafts'").fetchone() is None
        connection.execute('''CREATE TABLE IF NOT EXISTS calms_drafts (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            profile_ref TEXT NOT NULL REFERENCES profile_versions(ref) ON DELETE RESTRICT,
            draft_json TEXT NOT NULL, PRIMARY KEY(project_id,profile_ref))''')
        # Migration idempotente, sans réécriture des snapshots ni des actions.
        for row in (connection.execute("SELECT id,draft_answers_json FROM projects WHERE draft_answers_json IS NOT NULL") if migrate_drafts else ()):
            try:
                draft = self._convert_draft(CalmsDraft.from_json(row['draft_answers_json']), base)
            except ValueError:
                # Une donnée illisible reste consultable comme erreur sur son projet.
                raw = row['draft_answers_json']
            else:
                raw = draft.to_json()
            connection.execute("INSERT INTO calms_drafts VALUES (?,'baseline:1',?)", (row['id'], raw))
        connection.execute('''CREATE TRIGGER IF NOT EXISTS profile_content_immutable
            BEFORE UPDATE OF ref,slug,version,definition_json ON profile_versions
            BEGIN SELECT RAISE(ABORT, 'published profile is immutable'); END''')
        connection.execute('''CREATE TRIGGER IF NOT EXISTS profile_version_no_delete
            BEFORE DELETE ON profile_versions
            BEGIN SELECT RAISE(ABORT, 'published profile cannot be deleted'); END''')
        connection.execute('''CREATE TRIGGER IF NOT EXISTS baseline_protected
            BEFORE UPDATE ON profiles WHEN OLD.slug='baseline'
            BEGIN SELECT RAISE(ABORT, 'baseline protected'); END''')

    @staticmethod
    def _get_profile(connection, slug, version=None):
        row = connection.execute('''SELECT v.*,p.is_active FROM profile_versions v JOIN profiles p ON p.slug=v.slug
            WHERE v.slug=? AND ''' + ('v.is_current=1' if version is None else 'v.version=?'),
            (slug,) if version is None else (slug, version)).fetchone()
        if row is None:
            return None
        profile = EvaluationProfile.from_json(row['definition_json'])
        if profile.slug != row['slug'] or profile.version != row['version']:
            raise ValueError('Le profil enregistré est incohérent.')
        return replace(profile, is_active=bool(row['is_active']), is_current=bool(row['is_current']))

    def get_profile(self, slug, version=None):
        with self._connection() as connection:
            return self._get_profile(connection, slug, version)

    def list_profiles(self, *, include_archived=False):
        with self._connection() as connection:
            rows = connection.execute('SELECT slug FROM profiles' + ('' if include_archived else ' WHERE is_active=1')
                                      + " ORDER BY CASE WHEN slug='baseline' THEN 0 ELSE 1 END,slug").fetchall()
            return [self._get_profile(connection, row['slug']) for row in rows]

    def list_profile_versions(self, slug):
        with self._connection() as connection:
            rows = connection.execute('SELECT version FROM profile_versions WHERE slug=? ORDER BY version DESC', (slug,)).fetchall()
            return [self._get_profile(connection, slug, row['version']) for row in rows]

    def publish_profile(self, profile, *, expected_version=0):
        profile = EvaluationProfile.from_dict(profile.to_dict())
        if profile.slug == 'baseline':
            raise ValueError('Le référentiel de base est protégé. Choisissez un autre slug.')
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = self._get_profile(connection, profile.slug)
            if (current.version if current else 0) != expected_version:
                raise ValueError('Le profil existe déjà ou sa version courante a changé. Rechargez avant de publier.')
            if current and not current.is_active:
                raise ValueError('Ce profil est archivé. Choisissez un autre slug.')
            published = profile.published(expected_version + 1)
            from devops_flow.profile_io import export_markdown
            export_markdown(published)
            connection.execute('INSERT OR IGNORE INTO profiles VALUES (?,1)', (profile.slug,))
            connection.execute('UPDATE profile_versions SET is_current=0 WHERE slug=?', (profile.slug,))
            connection.execute('INSERT INTO profile_versions VALUES (?,?,?,?,1)',
                               (f'{profile.slug}:{published.version}', profile.slug, published.version, published.to_json()))
        return published

    def archive_profile(self, slug):
        if slug == 'baseline':
            raise ValueError('Le référentiel de base ne peut pas être supprimé.')
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if self._get_profile(connection, slug) is None:
                raise ValueError('Le profil demandé est inconnu.')
            for table in ('projects', 'diagnostics'):
                if connection.execute(f'''SELECT 1 FROM {table} t JOIN profile_versions v ON t.profile_ref=v.ref
                    WHERE v.slug=? LIMIT 1''', (slug,)).fetchone():
                    raise ValueError('Ce profil est référencé par un projet ou un diagnostic et ne peut pas être supprimé.')
            connection.execute('UPDATE profiles SET is_active=0 WHERE slug=?', (slug,))

    def _profile_for_project(self, connection, project_id):
        row = connection.execute('SELECT profile_ref FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise ValueError("Le projet demandé n'est plus disponible.")
        slug, version = row['profile_ref'].rsplit(':', 1)
        profile = self._get_profile(connection, slug, int(version))
        if profile is None:
            raise ValueError('Le profil imposé au projet est inconnu.')
        return profile

    def project_profile(self, project_id):
        with self._connection() as connection:
            return self._profile_for_project(connection, project_id)

    def update_project(self, project_id, name, description, *, profile_slug, profile_version):
        checked = Project.new(name, description)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._profile_for_project(connection, project_id)
            profile = self._get_profile(connection, profile_slug, profile_version)
            if profile is None or not profile.is_active:
                raise ValueError('Choisissez une version de profil actif connue.')
            connection.execute('UPDATE projects SET name=?,description=?,profile_ref=?,updated_at=? WHERE id=?',
                               (checked.name, checked.description, f'{profile.slug}:{profile.version}', checked.updated_at.isoformat(), project_id))
        return self.get_project(project_id)

    def profile_diagnostics(self, slug):
        with self._connection() as connection:
            rows = connection.execute('''SELECT d.id,d.project_id,d.created_at,d.profile_ref FROM diagnostics d
                JOIN profile_versions v ON d.profile_ref=v.ref WHERE v.slug=? ORDER BY d.created_at DESC,d.id''', (slug,)).fetchall()
        return [DiagnosticSummary(r['id'], r['project_id'], datetime.fromisoformat(r['created_at']), r['profile_ref']) for r in rows]
