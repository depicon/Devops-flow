from dataclasses import asdict
from pathlib import Path
import io
import json
import zipfile
import pytest

FIXTURES = Path(__file__).parent / 'fixtures'


def archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('Dockerfile', 'FROM alpine:latest\nUSER app')
    return buffer.getvalue()


async def test_zip_control_and_no_input_messages(gui, store):
    p = store.create_project('Scan sans CALMS')
    await gui.open(p.id, 'Diagnostic guidé')
    assert gui.element(f'scan_zip_{p.id}')
    await gui.click(f'scan_run_{p.id}')
    await gui.user.should_see('Choisissez une archive')
    await gui.set(f'scan_mode_{p.id}', 'Dossier local')
    await gui.click(f'scan_run_{p.id}')
    await gui.user.should_see('Renseignez le chemin')


async def test_local_scan_without_calms_and_temporary_result(gui, store):
    p = store.create_project('Scan seul')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'incomplete_project')
    await gui.user.should_see('Utilisation explicite du tag latest — Point à vérifier')
    await gui.user.should_see('Étape de tests identifiable dans GitLab CI — À confirmer')
    assert store.load_calms(p.id) is None
    assert gui.session.project().scan is not None
    await gui.open(p.id, 'Diagnostic guidé')
    assert gui.session.project().scan is None


async def test_invalid_source_clears_previous_result_and_is_neutral(gui, store, tmp_path):
    p = store.create_project('Erreur')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'healthy_project')
    assert gui.session.project().scan
    await gui.set(f'scan_path_{p.id}', str(tmp_path/'FAKE_SECRET_PATH'))
    assert gui.session.project().scan is None
    await gui.click(f'scan_run_{p.id}')
    await gui.user.should_see('Analyse refusée')
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)
    assert str(tmp_path) not in ' '.join(gui.texts)


async def test_switch_source_invalidates_result(gui, store):
    p = store.create_project('Changer de source')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, FIXTURES/'healthy_project')
    await gui.set(f'scan_mode_{p.id}', 'Archive ZIP')
    assert gui.session.project().scan is None


async def test_project_results_are_isolated(gui, store):
    a, b = store.create_project('A'), store.create_project('B')
    await gui.open(a.id, 'Diagnostic guidé')
    await gui.local(a.id, FIXTURES/'healthy_project')
    await gui.nav('Projets')
    await gui.click(f'open_{b.id}')
    await gui.nav('Diagnostic guidé')
    assert gui.session.project().scan is None


async def test_configuration_values_and_yaml_errors_not_rendered(gui, store, tmp_path):
    p = store.create_project('Preuves sûres')
    marker = 'FAKE_SECRET_UI_SCAN'
    (tmp_path/'compose.yaml').write_text(f'services: {{{marker}: {{privileged: true}}}}')
    (tmp_path/'.gitlab-ci.yml').write_text(f'test: [ {marker}')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, tmp_path)
    await gui.user.should_see('Étape de tests identifiable dans GitLab CI — Non analysable')
    assert marker not in ' '.join(gui.texts)
    assert marker not in json.dumps(asdict(gui.session.project().scan))


async def test_empty_local_scope_does_not_claim_ci_absent(gui, store, tmp_path):
    p = store.create_project('Périmètre vide')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, tmp_path)
    await gui.user.should_see('CI GitLab non observée dans ce périmètre')


async def test_zip_to_ui_bridge_with_simulated_uploader(gui, store):
    p = store.create_project('ZIP reçu')
    await gui.open(p.id, 'Diagnostic guidé')
    response = await gui.upload(p.id, archive())
    assert response.status_code == 200
    await gui.click(f'scan_run_{p.id}')
    await gui.user.should_see('Utilisation explicite du tag latest — Point à vérifier')
    assert gui.session.project().pending_zip is None
    await gui.upload(p.id, b'FAKE_SECRET_INVALID_ZIP')
    await gui.click(f'scan_run_{p.id}')
    await gui.user.should_see('Analyse refusée')
    assert gui.session.project().scan is None
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)


@pytest.mark.parametrize('source', ['local', 'zip'])
async def test_scan_context_survives_navigation_and_finalizes_matching_source(gui, store, source):
    p = store.create_project('Contexte conservé')
    await gui.open(p.id, 'Diagnostic guidé')
    if source == 'local':
        await gui.local(p.id, FIXTURES/'incomplete_project')
    else:
        assert (await gui.upload(p.id, archive())).status_code == 200
        await gui.click(f'scan_run_{p.id}')
    original = gui.session.project().scan
    await gui.nav('Projets')
    await gui.nav('Diagnostic guidé')
    assert gui.session.project().scan == original
    mode = 'Dossier local' if source == 'local' else 'Archive ZIP'
    assert gui.element(f'scan_mode_{p.id}').value == mode
    await gui.user.should_see('Source du dernier scan : ' + mode)
    await gui.set(f'include_scan_{p.id}', True)
    await gui.click(f'diagnostic_finalize_{p.id}')
    saved = store.latest_diagnostic(p.id)
    assert saved.snapshot.to_dict()['inputs']['scan']['source'] == source
    assert saved.snapshot.to_dict()['inputs']['scan']['files'] == list(original.files)
    await gui.set(f'scan_mode_{p.id}', 'Archive ZIP' if source == 'local' else 'Dossier local')
    await gui.set(f'include_scan_{p.id}', True)
    await gui.click(f'diagnostic_finalize_{p.id}')
    await gui.user.should_see('indisponible')
    assert store.latest_diagnostic(p.id) == saved


async def test_scan_context_is_isolated_when_returning_to_a_project(gui, store):
    a, b = store.create_project('A'), store.create_project('B')
    await gui.open(a.id, 'Diagnostic guidé')
    await gui.local(a.id, FIXTURES/'healthy_project')
    for p in (b, a):
        await gui.nav('Projets')
        await gui.click(f'open_{p.id}')
        await gui.nav('Diagnostic guidé')
        assert gui.element(f'scan_mode_{p.id}').value == ('Dossier local' if p == a else 'Archive ZIP')
        assert (gui.session.project().scan is not None) == (p == a)
