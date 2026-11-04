from dataclasses import replace
import io
import os
import zipfile

import pytest

from devops_flow.scanner import DEFAULT_LIMITS, ScanError, scan_project_local, scan_project_zip
from tests.test_project_scanner import zipped


def test_only_candidates_for_declared_language_are_read(monkeypatch):
    data = zipped({'package.json': '{}', 'main.py': 'PRIVATE_PYTHON', 'main.go': 'PRIVATE_GO',
                   'main.js': 'app.listen(8090)', '.git/main.js': 'PRIVATE_GIT'})
    opened = []
    original = zipfile.ZipFile.open
    def tracked(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original(self, member, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', tracked)
    result = scan_project_zip(data)
    assert set(opened) == {'package.json', 'main.js'}
    assert 'PRIVATE_' not in repr(result)


def test_three_candidates_per_component_and_cumulative_entry_budget(monkeypatch):
    files = {'package.json': '{}', **{name: 'app.listen(8080)' for name in ('index.js', 'main.js', 'server.js', 'src/main.js')}}
    data = zipped(files)
    opened = []
    original = zipfile.ZipFile.open
    def tracked(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original(self, member, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', tracked)
    scan_project_zip(data)
    assert len([name for name in opened if name.endswith('.js')]) == 3
    with pytest.raises(ScanError):
        scan_project_zip(data, limits=replace(DEFAULT_LIMITS, read_bytes=2))


@pytest.mark.parametrize('field,value', [('entrypoint', '../secret.py'), ('build_command', 'echo SECRET'),
    ('runtime', 'SECRET'), ('environment', 'TOKEN=SECRET')])
def test_direct_overrides_cannot_bypass_storage_validation(field, value):
    with pytest.raises(ScanError):
        scan_project_zip(zipped({'package.json': '{}'}), overrides={'.': {field: value}})


@pytest.mark.parametrize('revision', [-1, True, '1'])
def test_invalid_revision_is_rejected(revision, tmp_path):
    with pytest.raises(ScanError): scan_project_zip(zipped({}), override_revision=revision)
    with pytest.raises(ScanError): scan_project_local(tmp_path, override_revision=revision)


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_local_substitution_between_stat_and_open(kind, tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'package.json').write_text('{}')
    if kind == 'directory':
        (repo / 'service').mkdir()
        (repo / 'service' / 'package.json').write_text('{}')
    original = os.open
    def swapped(path, *args, **kwargs):
        if path == ('package.json' if kind == 'file' else 'service'):
            target = repo / path
            target.rename(repo / ('old-' + path))
            target.symlink_to(tmp_path / 'outside', target_is_directory=kind == 'directory')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swapped)
    monkeypatch.setattr(os, 'supports_dir_fd', os.supports_dir_fd | {swapped})
    with pytest.raises(ScanError): scan_project_local(repo)


def test_local_unicode_case_collisions_match_zip_policy(tmp_path):
    (tmp_path / 'backend').mkdir()
    (tmp_path / 'BACKEND').mkdir()
    with pytest.raises(ScanError): scan_project_local(tmp_path)


def test_compose_dockerfile_path_and_ci_string_reference():
    result = scan_project_zip(zipped({'backend/package.json': '{}', 'frontend/package.json': '{}',
        'backend/Dockerfile': 'FROM alpine:3', 'backend/.gitlab-ci.yml': 'test: {script: echo ok}',
        'compose.yaml': 'services: {api: {build: {context: ., dockerfile: backend/Dockerfile}}}',
        '.gitlab-ci.yml': 'include: backend/.gitlab-ci.yml'}))
    assert {r.kind for r in result.architecture.relations} == {'compose_build', 'ci_include'}
    assert next(r.confidence for r in result.architecture.relations if r.kind == 'compose_build') == 'medium'
    assert 'compose.yaml' in result.architecture.shared_files
    assert 'compose.yaml' not in result.architecture.unassigned_files


def test_invalid_yaml_never_invents_component_relations():
    result = scan_project_zip(zipped({'backend/package.json': '{}',
        'compose.yaml': 'services: {api: &x {build: backend}, other: *x}', 'README.md': ''}))
    assert not result.architecture.relations
    assert any(f.rule_id == 'CMP-001' and f.status == 'invalid' for f in result.findings)


def test_prefix_reader_rejects_stream_ignoring_requested_bound():
    from devops_flow.project_scanner import _read_prefix
    class Oversized:
        def readline(self, size): return b'x' * (size + 1)
    with pytest.raises(ScanError): _read_prefix(Oversized(), 8)


def test_env_suffix_cannot_become_declared_entrypoint(monkeypatch):
    data = zipped({'package.json': '{"main":".env.js"}', '.env.js': 'SECRET_SENTINEL'})
    original = zipfile.ZipFile.open
    def tracked(self, member, *args, **kwargs):
        assert member.filename != '.env.js'
        return original(self, member, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', tracked)
    result = scan_project_zip(data)
    assert '.env.js' not in result.files and 'SECRET_SENTINEL' not in repr(result)


def test_unicode_secret_local_root_is_rejected(tmp_path):
    root = tmp_path / 'ｓｅｃｒｅｔｓ'
    root.mkdir()
    (root / 'package.json').write_text('{}')
    with pytest.raises(ScanError): scan_project_local(root)
