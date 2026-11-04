"""Persistance additive des acquisitions et pages, intégrée à ProjectStore."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from devops_flow.documentation import DocumentationPage, MAX_PAGES, validate_page


class ProjectContentStore:
    def _initialize_content(self, connection):
        statements = (
            '''CREATE TABLE IF NOT EXISTS remote_repositories (
                project_id TEXT PRIMARY KEY REFERENCES projects(id),
                provider TEXT NOT NULL CHECK(provider IN ('gitlab','github')),
                host TEXT NOT NULL, project_path TEXT NOT NULL, ref TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS remote_acquisitions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                created_at TEXT NOT NULL, provenance_json TEXT NOT NULL,
                UNIQUE(id, project_id))''',
            '''CREATE TABLE IF NOT EXISTS diagnostic_remote_sources (
                diagnostic_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, acquisition_id TEXT NOT NULL,
                FOREIGN KEY(diagnostic_id, project_id) REFERENCES diagnostics(id, project_id),
                FOREIGN KEY(acquisition_id, project_id) REFERENCES remote_acquisitions(id, project_id))''',
            '''CREATE TABLE IF NOT EXISTS documentation_pages (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                current_version INTEGER NOT NULL CHECK(current_version >= 1), UNIQUE(id, project_id))''',
            '''CREATE TABLE IF NOT EXISTS documentation_revisions (
                page_id TEXT NOT NULL, project_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 1),
                title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
                markdown TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(page_id, version),
                FOREIGN KEY(page_id, project_id) REFERENCES documentation_pages(id, project_id))''',
            'CREATE INDEX IF NOT EXISTS remote_acquisition_project ON remote_acquisitions(project_id, created_at)',
        )
        for statement in statements:
            connection.execute(statement)
        for table in ('remote_acquisitions', 'documentation_revisions', 'diagnostic_remote_sources'):
            for operation in ('UPDATE', 'DELETE'):
                connection.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable content'); END''')

    def remote_repository(self, project_id):
        from devops_flow.remote import RemoteRepoRef
        with self._connection() as connection:
            row = connection.execute('SELECT provider, host, project_path, ref FROM remote_repositories WHERE project_id=?',
                                     (project_id,)).fetchone()
        return RemoteRepoRef(**dict(row)) if row else None

    def save_remote_acquisition(self, project_id, acquisition):
        # Le seul objet persistant est la projection explicite, jamais les fichiers ou le jeton.
        provenance = acquisition.provenance()
        reference = acquisition.reference
        identifier, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._require_project(connection, project_id)
            connection.execute('INSERT INTO remote_acquisitions VALUES (?,?,?,?)',
                               (identifier, project_id, now, json.dumps(provenance, ensure_ascii=False, allow_nan=False)))
            connection.execute('''INSERT INTO remote_repositories VALUES (?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET provider=excluded.provider,host=excluded.host,
                project_path=excluded.project_path,ref=excluded.ref''',
                               (project_id, reference.provider, reference.host, reference.project_path, reference.ref))
        return identifier

    def list_remote_acquisitions(self, project_id, *, diagnostic_id=None):
        with self._connection() as connection:
            if diagnostic_id:
                rows = connection.execute('''SELECT a.* FROM remote_acquisitions a JOIN diagnostic_remote_sources s
                    ON a.id=s.acquisition_id AND a.project_id=s.project_id
                    WHERE s.project_id=? AND s.diagnostic_id=?''', (project_id, diagnostic_id)).fetchall()
            else:
                rows = connection.execute('SELECT * FROM remote_acquisitions WHERE project_id=? ORDER BY created_at DESC LIMIT 50',
                                          (project_id,)).fetchall()
        return tuple({'id': row['id'], 'created_at': row['created_at'], **json.loads(row['provenance_json'])} for row in rows)

    @staticmethod
    def _require_project(connection, project_id):
        if connection.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
            raise ValueError('Le projet demandé n’est plus disponible.')

    def save_documentation(self, project_id, title, markdown, *, page_id=None, expected_version=0):
        title, markdown = validate_page(title, markdown)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._require_project(connection, project_id)
            if page_id is None:
                if expected_version != 0:
                    raise ValueError('La version de la nouvelle page est invalide.')
                count = connection.execute('SELECT count(*) FROM documentation_pages WHERE project_id=?', (project_id,)).fetchone()[0]
                if count >= MAX_PAGES:
                    raise ValueError('Ce projet contient déjà 100 pages.')
                page_id, version = str(uuid4()), 1
                connection.execute('INSERT INTO documentation_pages VALUES (?,?,?)', (page_id, project_id, version))
            else:
                row = connection.execute('SELECT current_version FROM documentation_pages WHERE id=? AND project_id=?',
                                         (page_id, project_id)).fetchone()
                if row is None:
                    raise ValueError('La page demandée n’est plus disponible.')
                if row[0] != expected_version:
                    raise ValueError('Cette page a changé. Votre saisie est conservée ; rechargez la dernière version avant de sauvegarder.')
                version = expected_version + 1
                connection.execute('UPDATE documentation_pages SET current_version=? WHERE id=? AND project_id=?',
                                   (version, page_id, project_id))
            now = datetime.now(timezone.utc).isoformat()
            connection.execute('INSERT INTO documentation_revisions VALUES (?,?,?,?,?,?)',
                               (page_id, project_id, version, title, markdown, now))
        return DocumentationPage(page_id, project_id, version, title, markdown, now)

    @staticmethod
    def _doc(row):
        return DocumentationPage(row['page_id'], row['project_id'], row['version'], row['title'], row['markdown'], row['created_at'])

    def list_documentation(self, project_id):
        with self._connection() as connection:
            rows = connection.execute('''SELECT r.* FROM documentation_revisions r JOIN documentation_pages p
                ON r.page_id=p.id AND r.project_id=p.project_id AND r.version=p.current_version
                WHERE p.project_id=? ORDER BY r.title COLLATE NOCASE,p.id''', (project_id,)).fetchall()
        return tuple(self._doc(row) for row in rows)

    def get_documentation(self, project_id, page_id, version=None):
        with self._connection() as connection:
            row = connection.execute('''SELECT r.* FROM documentation_revisions r JOIN documentation_pages p
                ON r.page_id=p.id AND r.project_id=p.project_id
                WHERE p.project_id=? AND p.id=? AND r.version=coalesce(?,p.current_version)''',
                                     (project_id, page_id, version)).fetchone()
        return self._doc(row) if row else None

    def documentation_versions(self, project_id, page_id):
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM documentation_revisions WHERE project_id=? AND page_id=? ORDER BY version DESC',
                                      (project_id, page_id)).fetchall()
        return tuple(self._doc(row) for row in rows)

    def restore_documentation(self, project_id, page_id, version, *, expected_version):
        page = self.get_documentation(project_id, page_id, version)
        if page is None:
            raise ValueError('La version demandée n’est plus disponible.')
        return self.save_documentation(project_id, page.title, page.markdown, page_id=page_id, expected_version=expected_version)
