import sqlite3

import pytest

from devops_flow.db import ProjectStore
from devops_flow.diagnostic import build_diagnostic
from devops_flow.scanner import scan_project_zip
from tests.test_project_scanner import zipped


def test_persisted_overrides_overlay_and_stale_finalization(store):
    p = store.create_project('A')
    assert store.load_stack_overrides(p.id) == (0, {})
    scan = scan_project_zip(zipped({'README.md': '', 'service/Dockerfile': 'FROM alpine'}))
    old = build_diagnostic(scan=scan, profile=store.project_profile(p.id))
    assert store.save_stack_override(p.id, 'service', {'language': 'Python', 'runtime': '3.12'}, expected_revision=0) == 1
    with pytest.raises(ValueError, match='déclarations'):
        store.save_diagnostic(p.id, 'stale', old)
    revision, values = store.load_stack_overrides(p.id)
    scan = scan_project_zip(zipped({'README.md': '', 'service/Dockerfile': 'FROM alpine'}), overrides=values, override_revision=revision)
    assert scan.architecture.components[0].declared
    assert scan.architecture.components[0].effective_signals[0].confidence == 'manual'
    snapshot = build_diagnostic(scan=scan, profile=store.project_profile(p.id))
    saved = store.save_diagnostic(p.id, 'fresh', snapshot)
    store.delete_stack_override(p.id, 'service', expected_revision=1)
    assert store.get_diagnostic(p.id, saved.id).snapshot == snapshot


def test_concurrency_isolation_restart_and_invalid_fields(store):
    a, b = store.create_project('A'), store.create_project('B')
    store.save_stack_override(a.id, '.', {'language': 'Rust'}, expected_revision=0)
    with pytest.raises(ValueError, match='autre session'):
        store.save_stack_override(a.id, '.', {'language': 'Java'}, expected_revision=0)
    again = ProjectStore(store.path)
    again.initialize()
    assert again.load_stack_overrides(a.id) == (1, {'.': {'language': 'Rust'}})
    assert again.load_stack_overrides(b.id) == (0, {})
    for path, fields in [('../escape', {'language': 'Python'}), ('.', {'port': 70000}),
                         ('.', {'build_command': 'echo SECRET'}), ('.', {'environment': 'TOKEN=SECRET'}),
                         ('.', {'entrypoint': '.env'}), ('.', {'runtime': '$(SECRET)'})]:
        with pytest.raises(ValueError):
            again.save_stack_override(a.id, path, fields, expected_revision=1)
    assert again.load_stack_overrides(a.id)[0] == 1


def test_override_budget_rollback_and_inactive_declarations(store):
    p = store.create_project('A')
    for n in range(20):
        store.save_stack_override(p.id, f'service{n}', {'language': 'Python'}, expected_revision=n)
    with pytest.raises(ValueError, match='Vingt'):
        store.save_stack_override(p.id, 'overflow', {'language': 'Java'}, expected_revision=20)
    revision, values = store.load_stack_overrides(p.id)
    assert revision == 20 and len(values) == 20
    result = scan_project_zip(zipped({'README.md': ''}), overrides=values, override_revision=revision)
    assert not result.architecture.components and result.architecture.warnings == ('inactive_override',)


def test_additive_migration_preserves_snapshots_actions_and_projects(store):
    p = store.create_project('A')
    snap = build_diagnostic(scan=scan_project_zip(zipped({'package.json': '{}'})), profile=store.project_profile(p.id))
    store.save_diagnostic(p.id, 'before', snap)
    with sqlite3.connect(store.path) as c:
        before = {table: c.execute(f'SELECT * FROM {table}').fetchall() for table in ('projects', 'diagnostics', 'actions')}
        c.execute('DROP TABLE component_stack_overrides')
        c.execute('DROP TABLE architecture_override_revisions')
    store.initialize()
    store.initialize()
    with sqlite3.connect(store.path) as c:
        assert before == {table: c.execute(f'SELECT * FROM {table}').fetchall() for table in before}
