"""Persistance additive des projections ; connexions fournies par ProjectStore."""
from dataclasses import replace
import json
from uuid import uuid4


class TrelloStore:
    def _initialize_trello(self, connection):
        for statement in (
            '''CREATE TABLE IF NOT EXISTS trello_projections (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                board_json TEXT, state TEXT NOT NULL CHECK(state IN ('building','ready','abandoned')),
                active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)), plan_json TEXT,
                UNIQUE(id, project_id))''',
            '''CREATE UNIQUE INDEX IF NOT EXISTS trello_active_project
                ON trello_projections(project_id) WHERE active=1''',
            '''CREATE UNIQUE INDEX IF NOT EXISTS trello_building_project
                ON trello_projections(project_id) WHERE state='building' ''',
            '''CREATE TABLE IF NOT EXISTS trello_projected_actions (
                projection_id TEXT NOT NULL, project_id TEXT NOT NULL,
                action_id TEXT NOT NULL REFERENCES actions(id), card_json TEXT NOT NULL,
                PRIMARY KEY(projection_id, action_id),
                FOREIGN KEY(projection_id, project_id) REFERENCES trello_projections(id, project_id))''',
            '''CREATE TABLE IF NOT EXISTS trello_operations (
                projection_id TEXT NOT NULL REFERENCES trello_projections(id),
                step TEXT NOT NULL, payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','uncertain','done')),
                result_json TEXT, retry_at REAL,
                PRIMARY KEY(projection_id, step))''',
        ):
            connection.execute(statement)

    def trello_projection(self, project_id, *, pending=False):
        with self._connection() as connection:
            predicate = "state='building'" if pending else 'active=1'
            row = connection.execute(f'SELECT * FROM trello_projections WHERE project_id=? AND {predicate}',
                                     (project_id,)).fetchone()
            return dict(row) if row else None

    def trello_projected(self, project_id, projection_id):
        with self._connection() as connection:
            return {r['action_id']: json.loads(r['card_json']) for r in connection.execute(
                'SELECT action_id,card_json FROM trello_projected_actions WHERE project_id=? AND projection_id=?',
                (project_id, projection_id))}

    def trello_start(self, plan):
        """Le service sérialise les étapes réseau ; cette réservation est transactionnelle."""
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if plan.mode == 'recreate':
                connection.execute("UPDATE trello_projections SET state=CASE WHEN active=1 THEN 'ready' ELSE 'abandoned' END, "
                                   "plan_json=NULL WHERE project_id=? AND state='building'", (plan.project_id,))
            if plan.mode == 'append':
                projection_id = plan.projection_id
                result = connection.execute("UPDATE trello_projections SET state='building',plan_json=? "
                                            "WHERE id=? AND project_id=? AND active=1 AND state='ready'",
                                            (plan.to_json(), projection_id, plan.project_id))
                if result.rowcount != 1:
                    raise ValueError('La projection a changé. Actualisez l’aperçu Trello.')
            else:
                projection_id = str(uuid4())
                # La reprise d'une génération créée volontairement ne doit pas
                # être interprétée comme une nouvelle demande de recréation.
                plan = replace(plan, projection_id=projection_id, mode='create')
                connection.execute('INSERT INTO trello_projections(id,project_id,state,plan_json) VALUES(?,?,?,?)',
                                   (projection_id, plan.project_id, 'building', plan.to_json()))
            return plan

    def trello_operation(self, projection_id, step, payload):
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM trello_operations WHERE projection_id=? AND step=?',
                                     (projection_id, step)).fetchone()
            if row:
                return dict(row), False
            value = json.dumps(payload, ensure_ascii=False)
            connection.execute("INSERT INTO trello_operations(projection_id,step,payload_json,state) VALUES(?,?,?,'pending')",
                               (projection_id, step, value))
            return {'state': 'pending', 'retry_at': None}, True

    def trello_checkpoint(self, projection_id, step, *, result=None, retry_at=None, uncertain=False):
        with self._connection() as connection:
            connection.execute('UPDATE trello_operations SET state=?,result_json=?,retry_at=? WHERE projection_id=? AND step=?',
                               ('uncertain' if uncertain else 'done', json.dumps(result, ensure_ascii=False)
                                if result is not None else None, retry_at, projection_id, step))
            if step == 'board' and result is not None:
                connection.execute('UPDATE trello_projections SET board_json=? WHERE id=?',
                                   (json.dumps(result, ensure_ascii=False), projection_id))

    def trello_card_complete(self, plan, card, result):
        with self._connection() as connection:
            # Vérifier aussi le projet de l'action avant d'établir la correspondance.
            row = connection.execute('SELECT 1 FROM actions WHERE id=? AND project_id=? AND diagnostic_id=?',
                                     (card.action_id, plan.project_id, plan.diagnostic_id)).fetchone()
            if row is None:
                raise ValueError('L’action ne correspond pas au projet et au diagnostic projetés.')
            connection.execute('INSERT OR IGNORE INTO trello_projected_actions VALUES(?,?,?,?)',
                               (plan.projection_id, plan.project_id, card.action_id, json.dumps(result, ensure_ascii=False)))

    def trello_finish(self, plan):
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('UPDATE trello_projections SET active=0 WHERE project_id=?', (plan.project_id,))
            connection.execute("UPDATE trello_projections SET active=1,state='ready',plan_json=NULL WHERE id=? AND project_id=?",
                               (plan.projection_id, plan.project_id))
