from dataclasses import replace
import io
import json
import os
import zipfile

import pytest

from devops_flow.architecture import ProjectArchitecture
from devops_flow.scanner import DEFAULT_LIMITS, ScanError, scan_project_local, scan_project_zip, scan_zip


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for p, b in files.items():
            z.writestr(p, b)
    return output.getvalue()


def fixture(tmp_path, files):
    for path, content in files.items():
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode() if isinstance(content, str) else content)
    return scan_project_local(tmp_path), scan_project_zip(zipped(files))


def test_multicomponent_paths_parity_and_nested_owner(tmp_path):
    files = {'pyproject.toml': '', 'backend/package.json': '{}',
             'backend/deep/Cargo.toml': '', 'backend/deep/Dockerfile': 'FROM alpine:latest',
             'frontend/package.json': '{}', '.env': 'SENTINEL', 'secrets/package.json': 'SENTINEL'}
    local, zip_result = fixture(tmp_path, files)
    assert local.architecture == zip_result.architecture
    assert local.findings == zip_result.findings
    assert [c.path for c in local.architecture.components] == ['.', 'backend', 'backend/deep', 'frontend']
    assert not local.architecture.components[1].devops.files
    assert local.architecture.components[2].devops.files == ('backend/deep/Dockerfile',)
    assert 'SENTINEL' not in json.dumps(local.architecture.to_dict())
    assert ProjectArchitecture.from_dict(local.architecture.to_dict()) == local.architecture


@pytest.mark.parametrize('prefix', ['', 'repository/'])
def test_wrapper_and_legacy_facade(prefix):
    r = scan_project_zip(zipped({prefix + 'package.json': '{}', prefix + 'backend/Dockerfile': 'FROM alpine'}))
    assert 'backend/Dockerfile' in r.files
    assert scan_zip(zipped({'README.md': '', 'backend/Dockerfile': 'FROM alpine'})).files == ()


@pytest.mark.parametrize('count', [20, 21])
def test_component_budget(count):
    data = zipped({f'c{i}/package.json': '{}' for i in range(count)})
    if count == 21:
        with pytest.raises(ScanError, match='Analyse refusée'):
            scan_project_zip(data)
    else:
        assert len(scan_project_zip(data).architecture.components) == 20


def test_unsupported_manifests_count_without_being_read(monkeypatch):
    data = zipped({'ruby/Gemfile': 'SENTINEL', 'dotnet/app.csproj': 'SENTINEL'})
    original = zipfile.ZipFile.open
    def tracked(self, info, *args, **kwargs):
        assert not info.filename.endswith(('Gemfile', '.csproj'))
        return original(self, info, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', tracked)
    r = scan_project_zip(data)
    assert len(r.architecture.components) == 2


@pytest.mark.parametrize('entry', ['../x', '/x', 'a/../x', 'a\\x', 'a/．．/x'])
def test_unsafe_zip_never_reads(entry, monkeypatch):
    data = zipped({'Dockerfile': 'FROM alpine', entry: ''})
    def forbidden(*args, **kwargs):
        pytest.fail('Lecture avant validation des métadonnées')
    monkeypatch.setattr(zipfile.ZipFile, 'open', forbidden)
    with pytest.raises(ScanError):
        scan_project_zip(data)


def test_local_skips_dependency_dirs_and_rejects_symlinks(tmp_path):
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules' / 'package.json').write_text('SENTINEL')
    assert not scan_project_local(tmp_path).architecture.components
    (tmp_path / 'link').symlink_to(tmp_path / 'node_modules', target_is_directory=True)
    with pytest.raises(ScanError):
        scan_project_local(tmp_path)


def test_local_entry_budget_and_read_budget(tmp_path):
    (tmp_path / 'package.json').write_text('{}')
    with pytest.raises(ScanError):
        scan_project_local(tmp_path, limits=replace(DEFAULT_LIMITS, entries=0))
    with pytest.raises(ScanError):
        scan_project_local(tmp_path, limits=replace(DEFAULT_LIMITS, read_bytes=1))


def test_entrypoint_read_is_bounded_and_never_persisted(tmp_path):
    files = {'package.json': '{}', 'main.js': '\n' * 200 + 'SENTINEL_AFTER_200', 'other.js': 'SENTINEL_OTHER'}
    local, archive = fixture(tmp_path, files)
    assert local.architecture == archive.architecture
    assert 'entrypoint_truncated' in local.architecture.warnings
    assert 'SENTINEL' not in repr(local)


def test_no_manifest_retains_six_project_checks(tmp_path):
    local, archive = fixture(tmp_path, {'README.md': '', 'nested/Dockerfile': 'FROM alpine:latest'})
    assert not local.architecture.components
    assert len(local.findings) == 6
    assert local.findings == archive.findings
    assert local.architecture.unassigned_files == ('nested/Dockerfile',)
