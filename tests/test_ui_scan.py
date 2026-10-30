from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import io
import json
import zipfile

import pytest

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"
FIXTURES = Path(__file__).parent / "fixtures"


def open_scan(project_id):
    at = AppTest.from_file(APP, default_timeout=10).run()
    at.button(key=f"open_{project_id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not at.exception
    return at


def local_scan(at, project_id, path):
    at.radio(key=f"scan_mode_{project_id}").set_value("Dossier local").run()
    at.text_input(key=f"scan_path_{project_id}").set_value(str(path)).run()
    at.button(key=f"scan_run_{project_id}").click().run()
    assert not at.exception
    return at


def test_zip_control_and_no_input_messages(store):
    project = store.create_project("Scan sans réponses CALMS")
    at = open_scan(project.id)
    assert len(at.get("file_uploader")) == 1
    at.button(key=f"scan_run_{project.id}").click().run()
    assert not at.exception
    assert any("Choisissez une archive" in info.value for info in at.info)
    at.radio(key=f"scan_mode_{project.id}").set_value("Dossier local").run()
    at.button(key=f"scan_run_{project.id}").click().run()
    assert any("Renseignez le chemin" in info.value for info in at.info)


def test_local_scan_without_calms_and_temporary_result(store):
    project = store.create_project("Scan seul")
    at = local_scan(open_scan(project.id), project.id, FIXTURES / "incomplete_project")
    assert at.success
    assert any("DOC-002 — Point à vérifier" in item.value for item in at.markdown)
    assert any("CI-002 — À confirmer" in item.value for item in at.markdown)
    assert store.load_calms(project.id) is None
    assert not at.metric
    assert not open_scan(project.id).success


def test_invalid_source_clears_previous_result_and_is_neutral(store, tmp_path):
    project = store.create_project("Erreur")
    at = local_scan(open_scan(project.id), project.id, FIXTURES / "healthy_project")
    assert at.success
    at.text_input(key=f"scan_path_{project.id}").set_value(str(tmp_path / "FAKE_SECRET_PATH")).run()
    assert not at.success
    at.button(key=f"scan_run_{project.id}").click().run()
    assert not at.exception
    assert at.error
    assert not at.success
    assert "FAKE_SECRET" not in at.error[0].value
    assert str(tmp_path) not in at.error[0].value


def test_switch_source_invalidates_result(store):
    project = store.create_project("Changer de source")
    at = local_scan(open_scan(project.id), project.id, FIXTURES / "healthy_project")
    at.radio(key=f"scan_mode_{project.id}").set_value("Archive ZIP").run()
    assert not at.exception
    assert not at.success


def test_project_results_are_isolated(store):
    first, second = store.create_project("A"), store.create_project("B")
    at = local_scan(open_scan(first.id), first.id, FIXTURES / "healthy_project")
    assert at.success
    at.sidebar.radio[0].set_value("Projets").run()
    at.button(key=f"open_{second.id}").click().run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    assert not at.success
    assert not at.exception


def test_configuration_values_and_yaml_errors_not_rendered(store, tmp_path):
    project = store.create_project("Preuves sûres")
    marker = "FAKE_SECRET_UI_SCAN"
    (tmp_path / "compose.yaml").write_text(f"services: {{{marker}: {{privileged: true}}}}")
    (tmp_path / ".gitlab-ci.yml").write_text(f"test: [ {marker}")
    at = local_scan(open_scan(project.id), project.id, tmp_path)
    assert at.success
    assert any("CI-002 — Non analysable" in item.value for item in at.markdown)
    for kind in (at.markdown, at.text, at.caption, at.error, at.warning):
        assert all(marker not in item.value for item in kind)
    assert marker not in json.dumps(asdict(at.session_state[f"scan_result_{project.id}"]))


def test_empty_local_scope_does_not_claim_ci_absent(store, tmp_path):
    project = store.create_project("Périmètre vide")
    at = local_scan(open_scan(project.id), project.id, tmp_path)
    assert at.success
    assert any("CI GitLab non observée dans ce périmètre" in item.value for item in at.markdown)


def test_zip_to_ui_bridge_with_simulated_uploader(store, monkeypatch):
    # AppTest ne fournit pas de méthode d'upload ; injection limitée à cette frontière.
    # Ce test ne prouve pas le transport du fichier depuis un navigateur.
    import devops_assist.ui.scan as ui_scan
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("Dockerfile", "FROM alpine:latest\nUSER app")
    uploaded = SimpleNamespace(size=len(data.getvalue()), getvalue=data.getvalue)
    monkeypatch.setattr(ui_scan.st, "file_uploader", lambda *args, **kwargs: uploaded)
    project = store.create_project("ZIP simulé")
    at = open_scan(project.id)
    at.button(key=f"scan_run_{project.id}").click().run()
    assert not at.exception
    assert at.success
    assert any("DOC-002 — Point à vérifier" in item.value for item in at.markdown)
    uploaded.getvalue = lambda: b"FAKE_SECRET_INVALID_ZIP"
    at.button(key=f"scan_run_{project.id}").click().run()
    assert not at.exception
    assert at.error
    assert not at.success
    assert "FAKE_SECRET" not in at.error[0].value


@pytest.mark.parametrize("source", ["local", "zip"])
def test_scan_context_survives_navigation_and_finalizes_matching_source(store, monkeypatch, source):
    import devops_assist.ui.scan as ui_scan
    project = store.create_project("Contexte conservé")
    at = open_scan(project.id)
    if source == "local":
        local_scan(at, project.id, FIXTURES / "incomplete_project")
    else:
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("Dockerfile", "FROM alpine:latest\nUSER app")
        uploaded = SimpleNamespace(size=len(data.getvalue()), getvalue=data.getvalue)
        monkeypatch.setattr(ui_scan.st, "file_uploader", lambda *a, **kw: uploaded)
        at.button(key=f"scan_run_{project.id}").click().run()
        # L'upload ne reste pas sélectionné après navigation ; le résultat reste identifié.
        monkeypatch.setattr(ui_scan.st, "file_uploader", lambda *a, **kw: None)
    original = at.session_state[f"scan_result_{project.id}"]
    at.sidebar.radio[0].set_value("Projets").run()
    at.sidebar.radio[0].set_value("Diagnostic guidé").run()
    mode = "Dossier local" if source == "local" else "Archive ZIP"
    assert at.radio(key=f"scan_mode_{project.id}").value == mode
    assert at.session_state[f"scan_result_{project.id}"] == original
    assert any(f"Source du dernier scan : {mode}" in item.value for item in at.caption)
    if source == "local":
        assert at.text_input(key=f"scan_path_{project.id}").value == str(FIXTURES / "incomplete_project")
    at.checkbox(key=f"include_scan_{project.id}").check().run()
    at.button(key=f"diagnostic_finalize_{project.id}").click().run()
    assert not at.exception
    saved = store.latest_diagnostic(project.id)
    assert saved.snapshot.to_dict()["inputs"]["scan"]["source"] == source
    assert saved.snapshot.to_dict()["inputs"]["scan"]["files"] == list(original.files)
    # Le changement explicite invalide aussi le scan après cet aller-retour.
    other = "Archive ZIP" if source == "local" else "Dossier local"
    at.radio(key=f"scan_mode_{project.id}").set_value(other).run()
    assert f"scan_result_{project.id}" not in at.session_state
    at.checkbox(key=f"include_scan_{project.id}").check().run()
    at.button(key=f"diagnostic_finalize_{project.id}").click().run()
    assert not at.exception
    assert any("indisponible" in item.value for item in at.error)
    assert store.latest_diagnostic(project.id) == saved


def test_scan_context_is_isolated_when_returning_to_a_project(store):
    first, second = store.create_project("A"), store.create_project("B")
    at = local_scan(open_scan(first.id), first.id, FIXTURES / "healthy_project")
    for project in (second, first):
        at.sidebar.radio[0].set_value("Projets").run()
        at.button(key=f"open_{project.id}").click().run()
        at.sidebar.radio[0].set_value("Diagnostic guidé").run()
        expected = "Dossier local" if project == first else "Archive ZIP"
        assert at.radio(key=f"scan_mode_{project.id}").value == expected
        assert (f"scan_result_{project.id}" in at.session_state) == (project == first)
        assert not at.exception
