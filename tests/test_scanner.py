from dataclasses import asdict, replace
import hashlib
import io
import json
import os
from pathlib import Path
import random
import stat
import struct
import warnings
import zipfile

import pytest

from devops_assist import scanner
from devops_assist.scanner import (
    ALLOWED_NAMES, DEFAULT_LIMITS, MIB, ScanError, _read_bounded, scan_local, scan_zip,
)

FIXTURES = Path(__file__).parent / "fixtures"


def zipped(entries, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize("fixture", ["healthy_project", "incomplete_project"])
@pytest.mark.parametrize("prefix", ["", "repository/"])
def test_local_zip_parity_and_no_mutations(fixture, prefix):
    root = FIXTURES / fixture
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    local = scan_local(root)
    archive = scan_zip(zipped([(prefix + p.name, p.read_bytes()) for p in root.iterdir()]))
    assert local.files == archive.files
    assert local.findings == archive.findings
    assert len(local.findings) == 6
    statuses = {f.rule_id: f.status for f in local.findings}
    if fixture == "healthy_project":
        assert set(statuses.values()) == {"observed"}
    else:
        assert statuses == {"DOC-001": "observed", "DOC-002": "risk", "DOC-003": "risk",
                            "CMP-001": "risk", "CI-001": "observed", "CI-002": "unknown"}
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}


def test_empty_and_irrelevant_sources_are_admissible(tmp_path):
    assert not scan_local(tmp_path).files
    assert not scan_zip(zipped([])).files
    assert not scan_zip(zipped([("repository/src/app.py", "ignored")])).files
    assert not scan_zip(zipped([("README.md", "ignored"), ("src/Dockerfile", "ignored")])).files


@pytest.mark.parametrize("name", [
    "../Dockerfile", "a/../Dockerfile", "/Dockerfile", "C:/Dockerfile", "C:Dockerfile",
    "\\\\server\\Dockerfile", "a\\Dockerfile", "a//Dockerfile", "./Dockerfile",
    "a/./Dockerfile", "Dockerfile\n", "Dockerfile\x00suffix", "a./Dockerfile",
    "a /Dockerfile", "a:stream/Dockerfile", "CON/Dockerfile", "a/．．/Dockerfile",
])
def test_zip_rejects_suspicious_paths(name):
    # ZipFile tronque les NUL à l'écriture ; les injecter dans le nom stocké.
    if "\x00" in name:
        data = zipped([("Dockerfile_suffix", "secret")]).replace(b"Dockerfile_suffix", b"Dockerfile\x00suffix")
    else:
        data = zipped([(name, "secret")])
    with pytest.raises(ScanError):
        scan_zip(data)


@pytest.mark.parametrize("names", [
    ["Dockerfile", "Dockerfile"], ["Dockerfile", "dockerfile"],
    ["repo/", "repo/"], ["a/Dockerfile", "b/Dockerfile"],
    ["repo/compose.yaml", "repo/ｃｏｍｐｏｓｅ.yaml"],
    ["repo", "repo/Dockerfile"],
])
def test_duplicate_and_ambiguous_roots_rejected(names):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        data = zipped([(name, b"") for name in names])
    with pytest.raises(ScanError):
        scan_zip(data)


@pytest.mark.parametrize("name", ["x" * 1025, "é" * 513, "a/" * 32 + "ignored", "a/" * 24000 + "ignored"],
                         ids=["bytes", "utf8_bytes", "depth", "audit_24000"])
def test_zip_rejects_overlong_or_deep_paths_before_any_content_is_opened(name, monkeypatch):
    data = zipped([("Dockerfile", "FROM alpine"), (name, b"")])
    def forbidden(*args, **kwargs):
        pytest.fail("Un contenu a été ouvert avant le contrôle de tous les chemins")
    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    with pytest.raises(ScanError) as error:
        scan_zip(data)
    assert error.value.code == "zip_path_budget"


@pytest.mark.parametrize("name", ["x" * 1024, "é" * 512, "repo/" + "a/" * 30 + "ignored", "repo/" + "a/" * 31],
                         ids=["bytes", "utf8_bytes", "file_depth", "directory_depth"])
def test_zip_accepts_path_length_and_depth_at_default_boundaries(name):
    assert scan_zip(zipped([(name, b"")])).files == ()


def test_zip_path_budgets_are_internal_parameters():
    data = zipped([("repo/Dockerfile", "FROM alpine")])
    assert scan_zip(data, limits=replace(DEFAULT_LIMITS, zip_path_bytes=15, zip_path_depth=2)).files == ("Dockerfile",)
    for limits in (replace(DEFAULT_LIMITS, zip_path_bytes=14), replace(DEFAULT_LIMITS, zip_path_depth=1)):
        with pytest.raises(ScanError) as error:
            scan_zip(data, limits=limits)
        assert error.value.code == "zip_path_budget"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("names,conflict", [
    (["repo/file", "repo/file/ignored"], True),
    (["repo/ｆｉｌｅ", "repo/FILE/ignored"], True),
    (["repo/", "repo/child/", "repo/child/ignored"], False),
    (["repo/one/ignored", "repo/two/ignored"], False),
])
def test_zip_path_tree_detects_conflicts_in_both_entry_orders(names, conflict, reverse):
    data = zipped([(name, b"") for name in (reversed(names) if reverse else names)])
    if conflict:
        with pytest.raises(ScanError) as error:
            scan_zip(data)
        assert error.value.code == "zip_path_conflict"
    else:
        assert scan_zip(data).files == ()


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_special_zip_members_rejected_even_if_not_allowlisted(mode):
    info = zipfile.ZipInfo("unrelated")
    info.create_system = 3
    info.external_attr = (mode | 0o600) << 16
    with pytest.raises(ScanError):
        scan_zip(zipped([(info, "FAKE_SECRET_LINK_TARGET")]))


def test_encrypted_zip_metadata_rejected():
    data = bytearray(zipped([("Dockerfile", "FROM alpine")]))
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<H", data, local + 6, 1)
    struct.pack_into("<H", data, central + 8, 1)
    with pytest.raises(ScanError):
        scan_zip(bytes(data))


@pytest.mark.parametrize("data", [b"", b"not a zip FAKE_SECRET_ZIP", zipped([("Dockerfile", "FROM alpine")])[:-12]])
def test_malformed_zip_neutral(data):
    with pytest.raises(ScanError) as error:
        scan_zip(data)
    assert "FAKE_SECRET" not in str(error.value)


def test_archive_byte_boundary():
    data = zipped([("Dockerfile", "FROM alpine")])
    assert scan_zip(data, limits=replace(DEFAULT_LIMITS, archive_bytes=len(data)))
    with pytest.raises(ScanError):
        scan_zip(data, limits=replace(DEFAULT_LIMITS, archive_bytes=len(data) - 1))


def test_default_entry_boundary():
    assert scan_zip(zipped([(f"ignored{i}", b"") for i in range(1000)]))
    with pytest.raises(ScanError):
        scan_zip(zipped([(f"ignored{i}", b"") for i in range(1001)]))


def test_default_file_byte_boundary_zip_and_local(tmp_path):
    content = b"#" + b"a" * (MIB - 1)
    path = tmp_path / "Dockerfile"
    path.write_bytes(content)
    assert scan_local(tmp_path).files == ("Dockerfile",)
    assert scan_zip(zipped([("Dockerfile", content)])).files == ("Dockerfile",)
    path.write_bytes(content + b"a")
    with pytest.raises(ScanError):
        scan_local(tmp_path)
    with pytest.raises(ScanError):
        scan_zip(zipped([("Dockerfile", content + b"a")]))


def test_announced_total_counts_ignored_files():
    data = zipped([("Dockerfile", b"1234"), (".env", b"1234")])
    assert scan_zip(data, limits=replace(DEFAULT_LIMITS, announced_bytes=8))
    with pytest.raises(ScanError):
        scan_zip(data, limits=replace(DEFAULT_LIMITS, announced_bytes=7))


def test_default_announced_total_boundary_without_decompressing_ignored_data():
    # Compression raisonnable (<100), archive <10 Mio, total annoncé 20 Mio.
    content = random.Random(42).randbytes(16384) * 64
    entries = [(f"ignored{i}", content) for i in range(20)]
    assert scan_zip(zipped(entries, zipfile.ZIP_DEFLATED)).files == ()
    with pytest.raises(ScanError):
        scan_zip(zipped([*entries, ("overflow", b"x")], zipfile.ZIP_DEFLATED))


def test_default_archive_byte_boundary():
    entries = [(f"ignored{i}", b"x" * MIB) for i in range(10)]
    extra = len(zipped(entries)) - DEFAULT_LIMITS.archive_bytes
    entries[-1] = (entries[-1][0], b"x" * (MIB - extra))
    data = zipped(entries)
    assert len(data) == DEFAULT_LIMITS.archive_bytes
    assert scan_zip(data)
    with pytest.raises(ScanError):
        scan_zip(data + b"x")


def test_default_cumulative_read_boundary_local(tmp_path):
    content = b"#" + b"x" * (MIB - 1)
    for name in ALLOWED_NAMES:
        (tmp_path / name).write_bytes(content)
    assert len(scan_local(tmp_path).files) == 6  # exactement 6 Mio lus


def test_crc_failure_is_neutral():
    data = zipped([("Dockerfile", "FROM alpine")]).replace(b"FROM alpine", b"FROM broken")
    with pytest.raises(ScanError):
        scan_zip(data)


def test_corrupt_deflate_is_neutral():
    data = bytearray(zipped([("Dockerfile", "FROM alpine")], zipfile.ZIP_DEFLATED))
    name_length, extra_length = struct.unpack_from("<HH", data, 26)
    data[30 + name_length + extra_length] = 7  # bloc DEFLATE de type réservé
    with pytest.raises(ScanError):
        scan_zip(bytes(data))


def test_compression_ratio_boundary():
    data = zipped([("Dockerfile", "FROM alpine")])  # stocké : ratio exactement 1
    assert scan_zip(data, limits=replace(DEFAULT_LIMITS, compression_ratio=1))
    with pytest.raises(ScanError):
        scan_zip(data, limits=replace(DEFAULT_LIMITS, compression_ratio=0))
    with pytest.raises(ScanError):
        scan_zip(zipped([(".env", b"0" * 100_000)], zipfile.ZIP_DEFLATED))


@pytest.mark.parametrize("source", ["zip", "local"])
def test_actual_cumulative_budget(source, tmp_path):
    entries = [("Dockerfile", b"1234"), ("compose.yaml", b"1234")]
    for name, content in entries:
        (tmp_path / name).write_bytes(content)
    def run(limit):
        limits = replace(DEFAULT_LIMITS, read_bytes=limit)
        return scan_zip(zipped(entries), limits=limits) if source == "zip" else scan_local(tmp_path, limits=limits)
    assert run(8)
    with pytest.raises(ScanError):
        run(7)


def test_actual_bytes_not_just_zip_metadata(monkeypatch):
    data = zipped([("Dockerfile", b"1234")])
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **kw: io.BytesIO(b"x" * 9))
    with pytest.raises(ScanError) as error:
        scan_zip(data, limits=replace(DEFAULT_LIMITS, file_bytes=8))
    assert error.value.code == "read_budget"


def test_real_read_handles_short_chunks_and_boundary():
    class ShortStream(io.BytesIO):
        def read(self, size):
            return super().read(min(size, 2))
    assert _read_bounded(ShortStream(b"12345678"), 8) == b"12345678"
    with pytest.raises(ScanError):
        _read_bounded(ShortStream(b"123456789"), 8)


def test_only_allowed_root_files_are_opened_local(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM alpine\nUSER app")
    for name in (".env", "key.pem", "app.py"):
        (tmp_path / name).write_text("FAKE_SECRET_DO_NOT_READ")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("FAKE_SECRET_DO_NOT_READ")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Dockerfile").write_text("FAKE_SECRET_DO_NOT_READ")
    opened = []
    real_open = os.open
    def tracked(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", tracked)
    # Garder la vérification de capacité OS pertinente avec le wrapper de test.
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {tracked})
    result = scan_local(tmp_path)
    assert result.files == ("Dockerfile",)
    assert all(not any(part in {".env", "key.pem", "app.py", ".git", "src"} for part in Path(p).parts) for p in opened)
    assert "FAKE_SECRET" not in json.dumps(asdict(result))


def test_only_allowed_zip_members_decompressed(monkeypatch):
    opened = []
    original = zipfile.ZipFile.open
    def tracked(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original(self, member, *args, **kwargs)
    data = zipped([("repo/Dockerfile", "FROM alpine"), ("repo/.env", "FAKE_SECRET"),
                   ("repo/.git/config", "FAKE_SECRET"), ("repo/key.pem", "FAKE_SECRET")])
    monkeypatch.setattr(zipfile.ZipFile, "open", tracked)
    assert scan_zip(data).files == ("Dockerfile",)
    assert opened == ["repo/Dockerfile"]


@pytest.mark.parametrize("root", [".git", ".env", ".ssh", ".aws", ".gnupg", "secrets"])
def test_secret_directory_cannot_become_zip_wrapper(root, monkeypatch):
    data = zipped([(root + "/Dockerfile", "FAKE_SECRET_NEVER_READ")])
    def forbidden(*args, **kwargs):
        pytest.fail("Un contenu situé dans un dossier secret a été ouvert")
    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    with pytest.raises(ScanError):
        scan_zip(data)


@pytest.mark.parametrize("kind", ["root", "parent", "file"])
def test_local_symlinks_rejected(tmp_path, kind):
    actual = tmp_path / "actual"
    actual.mkdir()
    if kind == "file":
        secret = tmp_path / ".env"
        secret.write_text("FAKE_SECRET")
        (actual / "Dockerfile").symlink_to(secret)
        path = actual
    else:
        link = tmp_path / "link"
        link.symlink_to(actual, target_is_directory=True)
        if kind == "parent":
            (actual / "child").mkdir()
            path = link / "child"
        else:
            path = link
    with pytest.raises(ScanError):
        scan_local(path)


def test_symlink_substitution_between_check_and_open(tmp_path, monkeypatch):
    config = tmp_path / "Dockerfile"
    config.write_text("FROM alpine")
    secret = tmp_path / ".env"
    secret.write_text("FAKE_SECRET_RACE")
    original = os.open
    def swapped(path, *args, **kwargs):
        if path == "Dockerfile":
            config.unlink()
            config.symlink_to(secret)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", swapped)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swapped})
    with pytest.raises(ScanError):
        scan_local(tmp_path)


def test_regular_file_substitution_between_check_and_open(tmp_path, monkeypatch):
    config = tmp_path / "Dockerfile"
    config.write_text("FROM alpine")
    other = tmp_path / "other"
    other.write_text("FAKE_SECRET_REPLACEMENT")
    original = os.open
    def swapped(path, *args, **kwargs):
        if path == "Dockerfile":
            other.replace(config)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", swapped)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swapped})
    with pytest.raises(ScanError) as error:
        scan_local(tmp_path)
    assert error.value.code == "local_changed_file"


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_local_special_files_rejected_without_blocking(tmp_path, kind):
    target = tmp_path / "Dockerfile"
    target.mkdir() if kind == "directory" else os.mkfifo(target)
    with pytest.raises(ScanError):
        scan_local(tmp_path)


@pytest.mark.parametrize("path", ["", "/not-present/FAKE_SECRET", "/tmp/../etc", "/tmp/.git", "/tmp/.ssh", "/tmp/.env"])
def test_local_bad_paths_neutral(path):
    with pytest.raises(ScanError) as error:
        scan_local(path)
    assert "FAKE_SECRET" not in str(error.value)
    assert path not in str(error.value) if path else True


def test_unsupported_os_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(ScanError) as error:
        scan_local(tmp_path)
    assert error.value.code == "unsupported_os"


def test_relative_root_cannot_hide_secret_parent(tmp_path, monkeypatch):
    secret = tmp_path / ".git"
    secret.mkdir()
    (secret / "Dockerfile").write_text("FAKE_SECRET_NEVER_READ")
    monkeypatch.chdir(secret)
    with pytest.raises(ScanError) as error:
        scan_local(".")
    assert error.value.code == "local_path"


def test_configuration_commands_never_run(tmp_path):
    marker = tmp_path / "EXECUTION_FORBIDDEN"
    files = {
        "Dockerfile": f"FROM alpine\nRUN touch {marker}\nUSER app",
        ".gitlab-ci.yml": f"test:\n  script: touch {marker}",
        "app.py": f"open({str(marker)!r}, 'w').close()",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    assert scan_local(tmp_path)
    assert scan_zip(zipped(list(files.items())))
    assert not marker.exists()
