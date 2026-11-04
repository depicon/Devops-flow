import asyncio
import json
from dataclasses import replace

import pytest
from nicegui import ui
from devops_flow.remote import RemoteAcquisition, RemoteRepoRef, RemoteMetadata, RemoteError
from devops_flow.scanner import ScanResult
from devops_flow.rules import evaluate_files
from devops_flow.documentation_pdf import PdfExport

pytestmark = pytest.mark.nicegui


def acquired():
    files={'Dockerfile': b'FROM alpine:latest\n'}
    return RemoteAcquisition(RemoteRepoRef('github','github.com','owner/repo','main'),
        RemoteMetadata('main','a'*40,'2026-11-02',({'sha':'a'*40,'author':'Auteur','date':'2026-11-02','message':'Mise à jour'},),
                       ({'id':7,'status':'success','ref':'main','duration':12},)),
        ScanResult('zip',tuple(files),evaluate_files(files)),('Python déclaré',),'api')


async def docs(gui, project_id):
    await gui.open(project_id)
    await gui.click('projects_docs')
    await asyncio.sleep(.1)


async def test_remote_scan_finalization_history_and_token_session_only(gui,store,monkeypatch):
    p=store.create_project('Distant')
    calls=[]
    def analyze(ref,token):calls.append((ref,token));return acquired()
    monkeypatch.setattr('devops_flow.ui.state.analyze_remote',analyze)
    await gui.open(p.id,'Diagnostic guidé')
    await gui.set(f'scan_mode_{p.id}','Dépôt distant')
    await gui.set(f'remote_provider_{p.id}','github')
    await gui.set(f'remote_url_{p.id}','https://github.com/owner/repo')
    await gui.set(f'remote_token_{p.id}','PRIVATE_TOKEN')
    await gui.click(f'scan_run_{p.id}')
    assert calls[0][1] == 'PRIVATE_TOKEN' and gui.session.project(p.id).scan is not None
    assert 'Python déclaré' in ' '.join(gui.texts)
    state=gui.session.project(p.id)
    state.include_calms=False
    state.include_scan=True
    d=await gui.session.finalize(p.id)
    assert store.list_remote_acquisitions(p.id,diagnostic_id=d.id)
    assert 'PRIVATE_TOKEN' not in store.path.read_bytes().decode('utf-8',errors='ignore')
    await gui.nav('Historique / Rapports')
    assert any('Mise à jour' in t for t in gui.texts)
    await gui.nav('Diagnostic guidé')
    await gui.click(f'remote_clear_token_{p.id}')
    assert state.remote_token == ''
    await gui.user.open('/')
    assert gui.session.project(p.id).remote_token == ''


async def test_remote_failure_is_neutral_and_not_admissible(gui,store,monkeypatch):
    p=store.create_project('Distant')
    def failed(*a):raise RemoteError('auth')
    monkeypatch.setattr('devops_flow.ui.state.analyze_remote',failed)
    await gui.open(p.id,'Diagnostic guidé')
    await gui.set(f'scan_mode_{p.id}','Dépôt distant')
    await gui.set(f'remote_url_{p.id}','https://gitlab.com/g/p')
    await gui.click(f'scan_run_{p.id}')
    assert any('Accès refusé' in t for t in gui.texts)
    assert gui.session.project(p.id).scan is None
    assert store.list_remote_acquisitions(p.id) == ()


async def test_late_remote_result_does_not_replace_source(gui,store,monkeypatch):
    p=store.create_project('Distant')
    await gui.open(p.id,'Diagnostic guidé')
    state=gui.session.project(p.id)
    state.mode='Dépôt distant'
    state.remote_url='https://github.com/a/b'
    def analyze(*args):
        import time
        time.sleep(.1)
        return acquired()
    monkeypatch.setattr('devops_flow.ui.state.analyze_remote',analyze)
    task=asyncio.create_task(gui.session.analyze(p.id))
    await asyncio.sleep(.03)
    gui.session.change_source(p.id,mode='Dossier local',path='/tmp')
    assert await task is False
    assert state.scan is None and store.list_remote_acquisitions(p.id) == ()


async def test_docs_empty_create_edit_restore_and_pdf_selection(gui,store,monkeypatch):
    p=store.create_project('Documentation')
    await docs(gui,p.id)
    assert 'Aucune page documentaire' in gui.texts
    await gui.click('docs_new')
    await gui.set('docs_title','Déploiement')
    await gui.set('docs_markdown','# Étapes\n\n```mermaid\nflowchart LR\nA-->B\n```')
    await gui.click('docs_save')
    page=store.list_documentation(p.id)[0]
    assert page.version == 1
    assert any(isinstance(e,ui.mermaid) for e in gui.user.client.elements.values())
    await gui.click('docs_edit')
    await gui.set('docs_markdown','Version deux')
    await gui.click('docs_save')
    assert store.get_documentation(p.id,page.id).version == 2
    await gui.set('docs_version',1)
    await gui.click('docs_restore')
    assert store.get_documentation(p.id,page.id).version == 3
    assert store.get_documentation(p.id,page.id).markdown.startswith('# Étapes')
    selected=[]
    def pdf(pages):selected.extend(pages);return PdfExport(b'%PDF-test','weasyprint',('Diagramme non rendu',))
    monkeypatch.setattr('devops_flow.ui.pages.documentation.export_documentation',pdf)
    await gui.set('docs_export_selection',[page.id])
    await gui.click('docs_export_pdf')
    assert selected[0].version == 3
    assert any('WeasyPrint' in t or 'weasyprint' in t for t in gui.texts)


async def test_documentation_conflict_preserves_draft(gui,store):
    p=store.create_project('Documentation')
    page=store.save_documentation(p.id,'Page','Initial')
    await docs(gui,p.id)
    await gui.click('docs_edit')
    await gui.set('docs_markdown','Ma saisie non enregistrée')
    store.save_documentation(p.id,'Page','Autre client',page_id=page.id,expected_version=1)
    await gui.click('docs_save')
    assert any('Cette page a changé' in t for t in gui.texts)
    assert gui.element('docs_markdown').value == 'Ma saisie non enregistrée'
    await gui.click('docs_reload')
    assert 'Autre client' in ' '.join(gui.texts)


async def test_docs_invalid_mermaid_and_xss_keep_text(gui,store):
    p=store.create_project('Documentation')
    store.save_documentation(p.id,'Page','Texte conservé\n<script>HIDDEN</script>\n```mermaid\n%%{init: {securityLevel: loose}}%%\nflowchart LR\n```')
    await docs(gui,p.id)
    assert any('Texte conservé' in t for t in gui.texts)
    assert any('directives' in t for t in gui.texts)
    assert not any('HIDDEN' in getattr(e,'_props',{}).get('innerHTML','') for e in gui.user.client.elements.values())


async def test_pdf_order_uses_visible_immutable_revisions(gui,store,monkeypatch):
    p=store.create_project('Ordre documentaire')
    first=store.save_documentation(p.id,'A','Révision affichée')
    second=store.save_documentation(p.id,'B','Deuxième page')
    await docs(gui,p.id)
    await gui.set('docs_export_selection',[first.id,second.id])
    await gui.click('docs_export_order')
    await gui.click('docs_down_'+first.id)
    state=gui.session.project(p.id).documentation
    assert state['export']==[second.id,first.id]
    # Une autre fenêtre publie sans actualiser les versions affichées par ce client.
    store.save_documentation(p.id,'A','Modification parallèle',page_id=first.id,expected_version=1)
    exported=[]
    def pdf(pages):exported.extend(pages);return PdfExport(b'%PDF-test','playwright')
    monkeypatch.setattr('devops_flow.ui.pages.documentation.export_documentation',pdf)
    await gui.click('docs_export_pdf')
    assert [page.id for page in exported]==[second.id,first.id]
    assert exported[1].version==1 and exported[1].markdown=='Révision affichée'
