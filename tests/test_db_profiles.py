from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import pytest
from devops_flow.db import ProjectStore
from devops_flow.calms import CalmsDraft
from devops_flow.diagnostic import build_diagnostic
from devops_flow.profiles import EvaluationProfile
from test_profiles import profile_data


def test_baseline_created_once_and_database_constraints(store):
    store.initialize()
    assert [(p.slug,p.version) for p in store.list_profiles()] == [('baseline',1)]
    project = store.create_project('Test')
    with store._connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE projects SET profile_ref='missing:1' WHERE id=?", (project.id,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE profile_versions SET definition_json='{}'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM profile_versions')
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE profiles SET is_active=0 WHERE slug='baseline'")
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_draft_isolation_and_explicit_upgrade(store):
    first = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    project = store.create_project('Équipe', profile_slug=first.slug)
    saved = store.save_calms(project.id, {'c1':2}, profile=first)
    second = store.publish_profile(first, expected_version=1)
    assert store.project_profile(project.id).version == 1
    with pytest.raises(ValueError, match='référencé'):
        store.archive_profile(first.slug)
    store.update_project(project.id, project.name, '', profile_slug=first.slug, profile_version=2)
    assert store.load_calms(project.id,profile=second) is None
    store.save_calms(project.id,{'c1':3},profile=second)
    store.update_project(project.id,project.name,'',profile_slug=first.slug,profile_version=1)
    assert store.load_calms(project.id,profile=first) == saved
    with pytest.raises(ValueError, match='profil'):
        store.save_calms(project.id,{},profile=second)


def test_concurrent_publication_has_one_winner(store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    def publish(_):
        try:
            return store.publish_profile(profile,expected_version=1).version
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(publish,range(2)))
    assert sorted(v for v in result if v is not None) == [2]
    assert len(store.list_profile_versions(profile.slug)) == 2


def test_additive_migration_preserves_legacy_snapshot_and_draft(tmp_path):
    path = tmp_path/'old.sqlite3'
    draft = CalmsDraft.new({'C1':2},{'C1':'Note conservée'})
    snapshot = build_diagnostic(answers=draft.answers).to_json()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY,name TEXT,description TEXT,created_at TEXT,updated_at TEXT,draft_answers_json TEXT)')
        db.execute('CREATE TABLE diagnostics (id TEXT PRIMARY KEY,project_id TEXT,submission_key TEXT UNIQUE,created_at TEXT,schema_version TEXT,questionnaire_version TEXT,rules_version TEXT,snapshot_json TEXT,UNIQUE(id,project_id))')
        when = draft.saved_at.isoformat()
        db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',('p','Ancien','',when,when,draft.to_json()))
        db.execute('INSERT INTO diagnostics VALUES (?,?,?,?,?,?,?,?)',('d','p','s',when,'diagnostic-v1','calms-v1',None,snapshot))
    store = ProjectStore(path)
    store.initialize()
    store.initialize()
    assert store.get_diagnostic('p','d').snapshot.to_json() == snapshot
    assert store.load_calms('p') == draft
    canonical = store.load_calms('p',profile=store.project_profile('p'))
    assert canonical.answers['c1'] == 2
    assert canonical.notes == {'c1':'Note conservée'}
    with store._connection() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT count(*) FROM calms_drafts').fetchone()[0] == 1


def test_failed_publication_rolls_back_current_version(store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    with store._connection() as db:
        db.execute("CREATE TRIGGER fail_publish BEFORE INSERT ON profile_versions BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.publish_profile(profile,expected_version=1)
    assert store.get_profile(profile.slug).version == 1
