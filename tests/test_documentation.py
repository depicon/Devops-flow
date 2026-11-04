from dataclasses import replace
import json
import sqlite3

import pytest

from devops_flow.db import ProjectStore
from devops_flow.diagnostic import build_diagnostic
from devops_flow.documentation import MAX_PAGE_BYTES, markdown_parts, safe_mermaid, sanitize_html
from devops_flow.remote import RemoteAcquisition, RemoteRepoRef, RemoteMetadata
from devops_flow.scanner import ScanResult
from devops_flow.rules import evaluate_files


def acquisition():
    return RemoteAcquisition(RemoteRepoRef('github','github.com','owner/repo','main'),
         RemoteMetadata('main','a'*40,'2026-11-02',({'sha':'a'*40,'author':'Auteur','date':'2026-11-02','message':'Changement'},),()),
         ScanResult('zip',(),evaluate_files({})), ('Python déclaré',), 'api')


def test_additive_migration_preserves_old_snapshots_and_idempotence(store):
    p = store.create_project('Ancien projet')
    snapshot = build_diagnostic(answers={})
    d = store.save_diagnostic(p.id, 'old', snapshot)
    before = store.get_diagnostic(p.id, d.id).snapshot.to_json()
    # Simule une base ayant seulement les tables antérieures.
    with store._connection() as c:
        for table in ('diagnostic_remote_sources','remote_acquisitions','remote_repositories','documentation_revisions','documentation_pages'):
            c.execute('DROP TABLE ' + table)
    store.initialize()
    store.initialize()
    assert store.get_diagnostic(p.id, d.id).snapshot.to_json() == before
    assert store.list_documentation(p.id) == ()
    assert store.list_remote_acquisitions(p.id) == ()


def test_pages_versions_conflict_restore_and_project_isolation(store):
    p, other = store.create_project('A'), store.create_project('B')
    first = store.save_documentation(p.id, 'Procédure', '# Bonjour')
    second = store.save_documentation(p.id, 'Procédure à jour', '# Modifié', page_id=first.id, expected_version=1)
    assert store.get_documentation(p.id, first.id, 1) == first
    assert store.list_documentation(p.id) == (second,)
    assert store.get_documentation(other.id, first.id) is None
    with pytest.raises(ValueError, match='changé'):
        store.save_documentation(p.id, 'Obsolète', 'secret draft', page_id=first.id, expected_version=1)
    with pytest.raises(ValueError):store.save_documentation(other.id, 'Autre', 'x', page_id=first.id, expected_version=2)
    restored = store.restore_documentation(p.id, first.id, 1, expected_version=2)
    assert restored.version == 3 and restored.markdown == first.markdown
    assert len(store.documentation_versions(p.id, first.id)) == 3
    restarted = ProjectStore(store.path)
    restarted.initialize()
    assert restarted.get_documentation(p.id, first.id) == restored


@pytest.mark.parametrize('title,body', [('', 'x'), ('x'*121,'x'), ('x','x'*(MAX_PAGE_BYTES+1)), ('x','nul\x00'), ('x\n','y')])
def test_page_validation_no_partial_write(store,title,body):
    p = store.create_project('A')
    with pytest.raises(ValueError):store.save_documentation(p.id,title,body)
    assert store.list_documentation(p.id) == ()


def test_unicode_page_size_and_count_boundaries(store):
    p = store.create_project('A')
    store.save_documentation(p.id,'Été','é'*(MAX_PAGE_BYTES//2))
    with pytest.raises(ValueError):store.save_documentation(p.id,'Trop','é'*(MAX_PAGE_BYTES//2+1))
    for i in range(99):store.save_documentation(p.id,str(i),'')
    with pytest.raises(ValueError,match='100 pages'):store.save_documentation(p.id,'101','')


def test_immutable_revision_and_acquisition(store):
    p = store.create_project('A')
    page = store.save_documentation(p.id,'x','y')
    identifier = store.save_remote_acquisition(p.id,acquisition())
    for statement, value in [('UPDATE documentation_revisions SET markdown=\'evil\' WHERE page_id=?', page.id),
                             ('DELETE FROM documentation_revisions WHERE page_id=?', page.id),
                             ('UPDATE remote_acquisitions SET provenance_json=\'{}\' WHERE id=?', identifier)]:
        with pytest.raises(sqlite3.IntegrityError):
            with store._connection() as c:c.execute(statement,(value,))


def test_acquisition_link_atomicity_and_reports_exclude_remote_metadata(store):
    from devops_flow.reports import build_export, render_json
    p, other = store.create_project('A'), store.create_project('B')
    a = acquisition()
    aid = store.save_remote_acquisition(p.id,a)
    assert store.remote_repository(p.id) == a.reference
    assert store.remote_repository(other.id) is None
    profile = store.project_profile(p.id)
    snap = build_diagnostic(scan=a.scan, profile=profile)
    d = store.save_diagnostic(p.id,'remote',snap,remote_acquisition_id=aid)
    assert store.list_remote_acquisitions(p.id,diagnostic_id=d.id)[0]['id'] == aid
    assert store.list_remote_acquisitions(other.id,diagnostic_id=d.id) == ()
    assert 'owner/repo' not in render_json(build_export(*store.diagnostic_state(p.id,d.id)))
    with pytest.raises(sqlite3.IntegrityError):
        store.save_diagnostic(other.id,'bad-link',snap,remote_acquisition_id=aid)
    assert store.list_diagnostics(other.id) == []


def test_markdown_parts_do_not_interpret_nested_fences():
    content = '# Titre\n```python\n```mermaid\nA\n```\nfin\n~~~mermaid\nflowchart LR\n A-->B\n~~~\n'
    parts = markdown_parts(content)
    assert sum(k == 'mermaid' for k,_ in parts) == 1
    assert 'A-->B' in parts[-1][1]


@pytest.mark.parametrize('source', ['%%{init: {securityLevel: loose}}%%\nflowchart LR',
    '---\nconfig:\n securityLevel: loose\n---\nflowchart LR', 'flowchart LR\n click A "https://evil"',
    'flowchart LR\n A["<img src=x>"]', 'flowchart LR\nA@{img:"x"}',
    'flowchart LR\nclassDef x fill:url(//evil.example/a)', 'flowchart LR\nclassDef x fill:u\\72l(x)', 'x'*(16*1024+1)])
def test_unsafe_mermaid_rejected(source):
    with pytest.raises(ValueError):safe_mermaid(source)


def test_safe_render_preserves_tables_and_blocks_active_content():
    assert safe_mermaid('flowchart LR\n A-->B')
    safe = sanitize_html('<script>SECRET</script><img src="https://evil"><p onclick="evil()">OK</p><a href="file:///secret">lien</a>')
    assert safe == '<p>OK</p><a>lien</a>'
