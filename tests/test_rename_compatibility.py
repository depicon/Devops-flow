"""Compatibilité des installations et documents antérieurs au renommage."""
from pathlib import Path

import pytest

from devops_flow.db import ProjectStore
from devops_flow.diagnostic import build_diagnostic
from devops_flow.profile_io import export_markdown, parse_markdown
from devops_flow.profiles import baseline_profile
from devops_flow.ui import application


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(application, 'ROOT', tmp_path)
    monkeypatch.delenv('DEVOPS_FLOW_DB_PATH', raising=False)
    monkeypatch.delenv('DEVOPS_ASSIST_DB_PATH', raising=False)
    (tmp_path / 'var').mkdir()
    return tmp_path


def test_database_new_installation_and_existing_files(installation):
    current = installation / 'var/devops_flow.sqlite3'
    previous = installation / 'var/devops_assist.sqlite3'
    assert application.database_path() == current
    assert not current.exists()
    previous.touch()
    assert application.database_path() == previous
    current.touch()
    assert application.database_path() == current


def test_database_configuration_precedence(installation, monkeypatch):
    monkeypatch.setenv('DEVOPS_ASSIST_DB_PATH', 'legacy.sqlite3')
    assert application.database_path() == Path('legacy.sqlite3')
    monkeypatch.setenv('DEVOPS_FLOW_DB_PATH', 'current.sqlite3')
    assert application.database_path() == Path('current.sqlite3')
    monkeypatch.setenv('DEVOPS_FLOW_DB_PATH', '')
    assert application.database_path() == Path('legacy.sqlite3')
    monkeypatch.setenv('DEVOPS_ASSIST_DB_PATH', '')
    assert application.database_path() == installation / 'var/devops_flow.sqlite3'


def test_existing_database_reopens_without_rewriting_snapshots(installation):
    previous = installation / 'var/devops_assist.sqlite3'
    store = ProjectStore(previous)
    store.initialize()
    project = store.create_project('Projet antérieur')
    store.save_calms(project.id, {'C1': 3})
    diagnostic = store.save_diagnostic(project.id, 'avant-renommage', build_diagnostic(answers={'C1': 3}))
    profile_json = store.project_profile(project.id).to_json()
    snapshot_json = diagnostic.snapshot.to_json()
    saved_project = store.get_project(project.id)
    reopened = ProjectStore(application.database_path())
    reopened.initialize()
    assert reopened.get_project(project.id) == saved_project
    assert reopened.get_diagnostic(project.id, diagnostic.id).snapshot.to_json() == snapshot_json
    assert reopened.project_profile(project.id).to_json() == profile_json
    assert reopened.load_calms(project.id) is not None
    assert not (installation / 'var/devops_flow.sqlite3').exists()


def test_historical_profile_contract_and_published_baseline_are_preserved():
    profile = baseline_profile()
    assert profile.author == 'DevOps Assist'
    assert profile.to_dict()['format'] == 'devops-assist-profile-v1'
    assert parse_markdown(export_markdown(profile)).profile.to_json() == profile.to_json()


async def test_new_name_is_visible_in_shell(gui):
    await gui.open()
    assert 'devops-flow' in gui.texts
    assert 'DevOps Assist' not in gui.texts
