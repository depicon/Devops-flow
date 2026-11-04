"""Lectures bornées, sans extraction, exécution, écriture ni source brute en sortie."""

from dataclasses import dataclass, field
import io
import os
from pathlib import Path
import stat
import unicodedata
import zipfile
import zlib

from devops_flow.rules import RULES_VERSION, TechnicalFinding, evaluate_files
from devops_flow.architecture import ProjectArchitecture

ALLOWED_NAMES = (
    "Dockerfile", "compose.yaml", "compose.yml", "docker-compose.yaml",
    "docker-compose.yml", ".gitlab-ci.yml",
)
SCANNER_VERSION = "scanner-v3"
MIB = 1024 * 1024
SECRET_DIRECTORIES = frozenset({".git", ".env", ".ssh", ".aws", ".gnupg", "secrets"})


@dataclass(frozen=True)
class ScanLimits:
    archive_bytes: int = 10 * MIB
    entries: int = 1000
    announced_bytes: int = 20 * MIB
    file_bytes: int = MIB
    read_bytes: int = 6 * MIB
    compression_ratio: int = 100
    zip_path_bytes: int = 1024
    zip_path_depth: int = 32
    components: int = 20


DEFAULT_LIMITS = ScanLimits()


class ScanError(ValueError):
    """Code contrôlé ; aucun détail provenant de l'entrée ou d'une exception."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("Analyse refusée : entrée inaccessible, non sûre ou hors limites.")


@dataclass(frozen=True)
class ScanResult:
    source: str
    files: tuple[str, ...]
    findings: tuple[TechnicalFinding, ...]
    scanner_version: str = SCANNER_VERSION
    rules_version: str = RULES_VERSION


@dataclass(frozen=True)
class ScopedTechnicalFinding(TechnicalFinding):
    scope_id: str = 'project'


@dataclass(frozen=True)
class ScanResultV2(ScanResult):
    scanner_version: str = 'scanner-v4'
    rules_version: str = 'rules-v4'
    architecture: ProjectArchitecture = ProjectArchitecture()
    directories: tuple[str, ...] = ('.',)
    override_revision: int = 0


def _result(source: str, files: dict[str, bytes]) -> ScanResult:
    # Les octets ne traversent pas la frontière du service.
    return ScanResult(source, tuple(sorted(files)), evaluate_files(files))


def _read_bounded(stream, limit: int) -> bytes:
    chunks = []
    remaining = limit + 1
    while remaining:
        chunk = stream.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise ScanError("read_budget")
    return data


def _zip_parts(info: zipfile.ZipInfo, limits: ScanLimits) -> tuple[str, ...]:
    name = info.orig_filename
    # Borner avant normalisation, validation des caractères ou construction de l'arbre.
    if (len(name) > limits.zip_path_bytes or len(name.encode("utf-8")) > limits.zip_path_bytes
            or name.removesuffix("/").count("/") + 1 > limits.zip_path_depth):
        raise ScanError("zip_path_budget")
    if (not name or name != info.filename or name.startswith(("/", "\\"))
            or "\\" in name or any(char in name for char in ':*?<>|')
            or any(unicodedata.category(char).startswith("C") for char in name)):
        raise ScanError("zip_path")
    parts = name.removesuffix("/").split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    if any(not p or p in {".", ".."} or p.endswith((" ", "."))
           or p.split(".")[0].casefold() in reserved for p in parts):
        raise ScanError("zip_path")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if (info.flag_bits & 0x41 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
            or (kind == stat.S_IFDIR and not info.is_dir())
            or (kind == stat.S_IFREG and info.is_dir())
            or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
        raise ScanError("zip_metadata")
    return tuple(parts)


@dataclass
class _ZipPathNode:
    children: dict[str, "_ZipPathNode"] = field(default_factory=dict)
    declared: bool = False
    is_file: bool = False


def _register_zip_path(root: _ZipPathNode, parts: tuple[str, ...], *, is_dir: bool) -> None:
    node = root
    for part in parts:
        if node.is_file:
            raise ScanError("zip_path_conflict")
        if part not in node.children:
            node.children[part] = _ZipPathNode()
        node = node.children[part]
    if node.declared:
        raise ScanError("zip_duplicate_or_path")
    if not is_dir and node.children:
        raise ScanError("zip_path_conflict")
    node.declared, node.is_file = True, not is_dir


def scan_zip(data: bytes, *, limits: ScanLimits = DEFAULT_LIMITS) -> ScanResult:
    if not isinstance(data, bytes) or len(data) > limits.archive_bytes:
        raise ScanError("archive_budget")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.entries:
                raise ScanError("entry_budget")
            members, path_tree, total = [], _ZipPathNode(), 0
            for info in infos:
                parts = _zip_parts(info, limits)
                normalized = tuple(unicodedata.normalize("NFKC", p).casefold() for p in parts)
                # Refuser aussi les noms qui deviennent ambigus après normalisation.
                if any(p in {".", ".."} or p.endswith((" ", "."))
                                             or any(char in p for char in '/\\:*?<>|') for p in normalized):
                    raise ScanError("zip_duplicate_or_path")
                _register_zip_path(path_tree, normalized, is_dir=info.is_dir())
                total += info.file_size
                if (info.file_size > limits.file_bytes or total > limits.announced_bytes
                        or info.file_size > limits.compression_ratio * info.compress_size):
                    raise ScanError("announced_budget")
                members.append((info, parts))
            # Une racine directe, ou un seul dossier enveloppe commun.
            direct = any(len(parts) == 1 and not info.is_dir() for info, parts in members)
            roots = {parts[0] for _, parts in members}
            if not direct and len(roots) > 1:
                raise ScanError("ambiguous_root")
            offset = 0 if direct else 1
            if offset and roots and next(iter(roots)).casefold() in SECRET_DIRECTORIES:
                raise ScanError("zip_secret_root")
            files, actual = {}, 0
            for info, parts in members:
                if info.is_dir() or len(parts) != offset + 1 or parts[-1] not in ALLOWED_NAMES:
                    continue
                with archive.open(info) as stream:
                    content = _read_bounded(stream, min(limits.file_bytes, limits.read_bytes - actual))
                if len(content) != info.file_size:
                    raise ScanError("zip_size")
                actual += len(content)
                files[parts[-1]] = content
            return _result("zip", files)
    except ScanError:
        raise
    except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError, zipfile.BadZipFile, zlib.error):
        raise ScanError("invalid_zip") from None


def _open_root(path: str | Path) -> int:
    # Ouvrir chaque composant relativement au descripteur parent, sans resolve()
    # ni fenêtre entre une validation de lien et une ouverture qui le suivrait.
    if (os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd
            or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")):
        raise ScanError("unsupported_os")
    raw = os.fspath(path)
    if (not isinstance(raw, str) or not raw.strip() or "\x00" in raw
            or any(unicodedata.category(c).startswith("C") for c in raw)):
        raise ScanError("local_path")
    parts = Path(raw).parts
    absolute = Path(raw) if Path(raw).is_absolute() else Path.cwd() / raw
    if ".." in parts or any(p.casefold() in SECRET_DIRECTORIES
                            for p in absolute.parts):
        raise ScanError("local_path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def scan_local(path: str | Path, *, limits: ScanLimits = DEFAULT_LIMITS) -> ScanResult:
    try:
        root = _open_root(path)
        try:
            files, actual = {}, 0
            for name in ALLOWED_NAMES:
                try:
                    before = os.stat(name, dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(before.st_mode):
                    raise ScanError("local_special_file")
                if before.st_size > limits.file_bytes:
                    raise ScanError("file_budget")
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(opened.st_mode)
                            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                            or opened.st_size > limits.file_bytes):
                        raise ScanError("local_changed_file")
                    content = _read_bounded(stream, min(limits.file_bytes, limits.read_bytes - actual))
                    after = os.fstat(stream.fileno())
                    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ScanError("local_changed_file")
                actual += len(content)
                files[name] = content
            return _result("local", files)
        finally:
            os.close(root)
    except ScanError:
        raise
    except (OSError, ValueError, TypeError):
        raise ScanError("local_unavailable") from None


def scan_project_zip(data: bytes, *, limits: ScanLimits = DEFAULT_LIMITS, overrides=None,
                     override_revision=0):
    """Nouvelle API multi-composants ; scan_zip conserve le contrat historique."""
    from devops_flow.project_scanner import scan_project_zip as scan
    return scan(data, limits=limits, overrides=overrides, override_revision=override_revision)


def scan_project_local(path: str | Path, *, limits: ScanLimits = DEFAULT_LIMITS, overrides=None,
                       override_revision=0):
    from devops_flow.project_scanner import scan_project_local as scan
    return scan(path, limits=limits, overrides=overrides, override_revision=override_revision)
