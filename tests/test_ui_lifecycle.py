"""Courses et isolation propres à une interface à événements."""
import asyncio
import threading
from pathlib import Path

from devops_flow.ui.state import Session

FIXTURES = Path(__file__).parent/'fixtures'


async def test_two_clients_have_independent_projects_sources_and_unsaved_answers(gui, create_user, store):
    a, b = store.create_project('A'), store.create_project('B')
    await gui.open(a.id, 'Diagnostic guidé')
    await gui.set(f'calms_{a.id}_C1', 3)
    await gui.local(a.id, FIXTURES/'healthy_project')
    first = gui.session
    other = create_user()
    await other.open('/')
    second = other.client.devops_shell.session
    assert second is not first and second.active_project_id is None
    assert not second.projects
    other.find(marker=f'open_{b.id}').click()
    await asyncio.sleep(.03)
    assert second.active_project_id == b.id
    assert first.active_project_id == a.id
    assert first.project().scan is not None
    assert first.project().answers['c1'] == 3
    assert store.load_calms(a.id) is None
    await other.http_client.aclose()


async def test_late_scan_cannot_replace_result_after_source_change(gui, store, monkeypatch):
    await gui.open()
    p = store.create_project('Course')
    session = Session(store, active_project_id=p.id)
    session.change_source(p.id, mode='Dossier local', path=str(FIXTURES/'healthy_project'))
    started, release = threading.Event(), threading.Event()
    from devops_flow.scanner import scan_local
    result = scan_local(FIXTURES/'healthy_project')
    def delayed(path):
        started.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr('devops_flow.ui.state.scan_local', delayed)
    task = asyncio.create_task(session.analyze(p.id))
    try:
        for _ in range(100):
            if started.is_set(): break
            await asyncio.sleep(.01)
        assert started.is_set()
        session.change_source(p.id, mode='Archive ZIP')
    finally:
        release.set()
    assert await task is False
    assert session.project().scan is None


async def test_concurrent_finalization_reuses_submission_and_preserves_history(gui, store):
    await gui.open()
    p = store.create_project('Double pression')
    store.save_calms(p.id, {})
    session = Session(store, active_project_id=p.id)
    session.project().include_calms = True
    results = await asyncio.gather(session.finalize(p.id), session.finalize(p.id))
    assert results[0].id == results[1].id
    assert len(store.list_diagnostics(p.id)) == 1


async def test_source_changed_during_finalization_creates_nothing(gui, store, monkeypatch):
    await gui.open()
    p = store.create_project('Source modifiée')
    store.save_calms(p.id, {})
    session = Session(store, active_project_id=p.id)
    session.project().include_calms = True
    original = store.load_calms
    started, release = threading.Event(), threading.Event()
    def delayed(project_id, **kwargs):
        started.set()
        assert release.wait(5)
        return original(project_id, **kwargs)
    monkeypatch.setattr(store, 'load_calms', delayed)
    task = asyncio.create_task(session.finalize(p.id))
    try:
        for _ in range(100):
            if started.is_set(): break
            await asyncio.sleep(.01)
        assert started.is_set()
        session.change_source(p.id, mode='Dossier local')
    finally:
        release.set()
    import pytest
    with pytest.raises(ValueError, match='sources ont changé'):
        await task
    assert store.latest_diagnostic(p.id) is None


async def test_pending_archive_released_on_navigation_and_client_disposal(gui, store):
    p = store.create_project('Mémoire')
    await gui.open(p.id, 'Diagnostic guidé')
    state = gui.session.project()
    state.pending_zip = b'archive fictive'
    await gui.nav('Projets')
    assert state.pending_zip is None
    session = gui.session
    gui.user.client.delete()
    assert session.projects == {}


async def test_late_scan_does_not_display_false_success(gui, store, monkeypatch):
    p = store.create_project('Retour d’un ancien scan')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.set(f'scan_mode_{p.id}', 'Dossier local')
    await gui.set(f'scan_path_{p.id}', str(FIXTURES/'healthy_project'))
    from devops_flow.scanner import scan_local
    result = scan_local(FIXTURES/'healthy_project')
    started, release = threading.Event(), threading.Event()
    def delayed(path):
        started.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr('devops_flow.ui.state.scan_local', delayed)
    gui.user.find(marker=f'scan_run_{p.id}').click()
    try:
        for _ in range(100):
            if started.is_set(): break
            await asyncio.sleep(.01)
        assert started.is_set()
        with gui.user:
            gui.element(f'scan_path_{p.id}').value = str(FIXTURES/'incomplete_project')
    finally:
        release.set()
    await gui.idle()
    await gui.user.should_see('La source a changé pendant l’analyse')
    await gui.user.should_not_see('Scan terminé')
    assert gui.session.project().scan is None
