"""Déclarations de stack projet : migration additive et révision transactionnelle."""
import json
import re

from devops_flow.architecture import relative_path
from devops_flow.signals import FIELDS, Signal
from devops_flow.signals.parsing import runtime

MANUAL_COMMANDS = frozenset({'python -m build', 'pytest', 'python -m unittest', 'npm run build',
    'npm run test', 'go build ./...', 'go test ./...', 'mvn package', 'mvn test',
    'gradle build', 'gradle test', 'cargo build', 'cargo test'})


def validate_override(path, values):
    relative_path(path, root=True)
    if not isinstance(values, dict) or not values or set(values) - set(FIELDS):
        raise ValueError('Renseignez au moins un champ de stack reconnu.')
    cleaned = {}
    for field, value in values.items():
        if isinstance(value, str):
            value = value.strip()
        Signal(field, value, 'manual')
        if value is None or value == '':
            raise ValueError('Une déclaration ne peut pas être vide.')
        if field == 'runtime' and not runtime(value):
            raise ValueError('Indiquez une version ou une contrainte numérique.')
        if field in {'language', 'framework'} and not (isinstance(value, str) and re.fullmatch(r'[\w .+#-]{1,80}', value)):
            raise ValueError('Nom de technologie invalide.')
        if field == 'entrypoint': relative_path(value)
        if field in {'build_command', 'test_command'} and value not in MANUAL_COMMANDS:
            raise ValueError('Choisissez une commande usuelle reconnue.')
        if field == 'service' and value not in {'postgres', 'mysql', 'redis', 'rabbitmq', 'database', 'queue', 'cache'}:
            raise ValueError('Type de service non reconnu.')
        cleaned[field] = value
    return cleaned


class ArchitectureStore:
    def _initialize_architecture(self, connection):
        connection.execute('''CREATE TABLE IF NOT EXISTS architecture_override_revisions (
            project_id TEXT PRIMARY KEY NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK(revision >= 0))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS component_stack_overrides (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            component_path TEXT NOT NULL, fields_json TEXT NOT NULL,
            PRIMARY KEY(project_id, component_path))''')

    @staticmethod
    def _override_revision(connection, project_id):
        row = connection.execute('SELECT revision FROM architecture_override_revisions WHERE project_id=?', (project_id,)).fetchone()
        return row['revision'] if row else 0

    def load_stack_overrides(self, project_id):
        with self._connection() as connection:
            connection.execute('BEGIN')
            if connection.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
                raise ValueError('Projet indisponible.')
            revision = self._override_revision(connection, project_id)
            rows = connection.execute('SELECT component_path,fields_json FROM component_stack_overrides WHERE project_id=? ORDER BY component_path', (project_id,)).fetchall()
            values = {r['component_path']: validate_override(r['component_path'], json.loads(r['fields_json'])) for r in rows}
            return revision, values

    def save_stack_override(self, project_id, path, values, *, expected_revision):
        cleaned = validate_override(path, values)
        return self._write_override(project_id, path, cleaned, expected_revision)

    def delete_stack_override(self, project_id, path, *, expected_revision):
        relative_path(path, root=True)
        return self._write_override(project_id, path, None, expected_revision)

    def _write_override(self, project_id, path, values, expected):
        if type(expected) is not int or expected < 0:
            raise ValueError('Révision de déclaration invalide.')
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            revision = self._override_revision(connection, project_id)
            if revision != expected:
                raise ValueError('Les déclarations ont changé dans une autre session. Rechargez avant de réessayer.')
            if values is None:
                connection.execute('DELETE FROM component_stack_overrides WHERE project_id=? AND component_path=?', (project_id, path))
            else:
                connection.execute('INSERT INTO component_stack_overrides VALUES (?,?,?) ON CONFLICT(project_id,component_path) DO UPDATE SET fields_json=excluded.fields_json',
                                   (project_id, path, json.dumps(values, ensure_ascii=False, sort_keys=True)))
                if connection.execute('SELECT count(*) FROM component_stack_overrides WHERE project_id=?', (project_id,)).fetchone()[0] > 20:
                    raise ValueError('Vingt déclarations maximum par projet.')
            connection.execute('INSERT INTO architecture_override_revisions VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision', (project_id, revision + 1))
            return revision + 1
