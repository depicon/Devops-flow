"""Acquisition multi-composants en mémoire ; aucune exécution ni octet brut en sortie."""
from dataclasses import replace
import io
import os
from pathlib import Path, PurePosixPath
import stat
import unicodedata
import zipfile
import zlib

from devops_flow.architecture import (Component, ComponentDevOpsState, ProjectArchitecture,
    EXCLUDED_DIRECTORIES, component_id, is_manifest, relative_path)
from devops_flow.rules import evaluate_files
from devops_flow.scanner import (ALLOWED_NAMES, DEFAULT_LIMITS, ScanError, ScanResultV2, ScopedTechnicalFinding,
    SECRET_DIRECTORIES, _open_root, _read_bounded, _register_zip_path, _zip_parts, _ZipPathNode)
from devops_flow.signals import Signal

AUXILIARY_NAMES = frozenset({'.python-version', '.nvmrc', '.node-version', 'tsconfig.json',
    'rust-toolchain', 'rust-toolchain.toml'})
READ_MANIFESTS = frozenset({'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt',
    'Pipfile', 'package.json', 'go.mod', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'Cargo.toml'})
ENTRY_BYTES = 64 * 1024
ENTRY_LINES = 200
ENTRY_COUNT = 3


def _excluded(parts):
    normalized = (unicodedata.normalize('NFKC', p).casefold() for p in parts)
    return any(p in EXCLUDED_DIRECTORIES or p.startswith('.env.') for p in normalized)


def _base_allowed(path):
    name = path.rsplit('/', 1)[-1]
    return name in ALLOWED_NAMES or name in AUXILIARY_NAMES or name in READ_MANIFESTS


def _owner(path, components):
    directory = str(PurePosixPath(path).parent)
    parents = [p for p in components if p == '.' or directory == p or directory.startswith(p + '/')]
    return max(parents, key=lambda p: len(p.split('/')) if p != '.' else 0, default=None)


def _component_paths(paths, directories, overrides, limits):
    from devops_flow.component_overrides import validate_override
    components = {str(PurePosixPath(p).parent) for p in paths if is_manifest(p)}
    for path, values in (overrides or {}).items():
        validate_override(path, values)
        if path in directories:
            components.add(path)
    if len(components) > min(limits.components, 20):
        raise ScanError('component_budget')
    return tuple(sorted(components))


def _entry_candidates(paths, components, files):
    # Seuls des noms conventionnels ou un point d'entrée explicitement autorisé.
    from devops_flow.signals.entrypoints import declared_candidates
    candidates = set()
    for component in components:
        prefix = '' if component == '.' else component + '/'
        own = {p[len(prefix):]: b for p, b in files.items() if _owner(p, components) == component}
        declared = {prefix + p for p in declared_candidates(own)}
        conventional = set()
        supported_names = set()
        if set(own) & {'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile'}:
            supported_names.update({'app.py', 'main.py', 'manage.py', 'wsgi.py', 'asgi.py'})
        if 'package.json' in own:
            supported_names.update({'index.js', 'index.ts', 'index.mjs', 'server.js', 'server.ts', 'main.js', 'main.ts'})
        if 'go.mod' in own:
            supported_names.add('main.go')
        if 'Cargo.toml' in own:
            supported_names.add('main.rs')
        for path in paths:
            if _owner(path, components) != component:
                continue
            local = path[len(prefix):]
            name = local.rsplit('/', 1)[-1]
            if (name in supported_names and (('/' not in local) or local.startswith(('src/', 'cmd/')))
                    or (set(own) & {'pom.xml', 'build.gradle', 'build.gradle.kts'}
                        and local.startswith('src/main/java/') and name.endswith('Application.java'))):
                conventional.add(path)
        candidates.update(sorted(p for p in (declared | conventional) & set(paths)
                                 if _owner(p, components) == component)[:ENTRY_COUNT])
    return candidates


def _assemble(source, paths, directories, files, overrides, revision, limits, warnings=()):
    from devops_flow.signals.registry import default_registry
    components = _component_paths(paths, directories, overrides, limits)
    configs = {p: b for p, b in files.items() if p.rsplit('/', 1)[-1] in ALLOWED_NAMES}
    result_components, findings = [], []
    for path in components:
        prefix = '' if path == '.' else path + '/'
        own = {p[len(prefix):]: b for p, b in files.items() if _owner(p, components) == path}
        signals = tuple(replace(s, file=prefix + s.file if s.file else None,
                                value=prefix + s.value if s.field == 'entrypoint' and s.value else s.value)
                        for s in default_registry().scan(own))
        manual = tuple(Signal(field, value, 'manual', reason='user_declaration')
                       for field, value in (overrides or {}).get(path, {}).items())
        signals += manual
        warnings = (*warnings, *(s.reason for s in signals if s.reason in {'invalid_manifest', 'unresolved_manifest'}))
        for stack in {s.stack for s in signals}:
            for field in ('runtime', 'port'):
                if len({s.value for s in signals if s.stack == stack and s.field == field and s.value is not None and s.confidence != 'manual'}) > 1:
                    warnings = (*warnings, 'conflicting_signals')
        own_configs = tuple(sorted(p for p in configs if _owner(p, components) == path))
        missing = tuple(name for name in ('Dockerfile', '.gitlab-ci.yml')
                        if not any(p.rsplit('/', 1)[-1] == name for p in own_configs))
        result_components.append(Component(component_id(path), path,
            tuple(sorted(p for p in paths if is_manifest(p) and str(PurePosixPath(p).parent) == path)),
            signals or (Signal('language', None, 'unknown', reason='unsupported_manifest'),),
            ComponentDevOpsState(own_configs, (), missing), path in (overrides or {})))
        # Adaptation par fichier : plusieurs variantes dans un périmètre ne s'écrasent pas.
        findings.extend(_evaluate_scope({p: configs[p] for p in own_configs}, component_id(path)))
    from devops_flow.architecture_links import link_configurations
    result_components, relations, linked_inputs = link_configurations(result_components, configs)
    for component in result_components:
        if component.id in linked_inputs:
            combined = {p: configs[p] for p in component.devops.files}
            combined.update(linked_inputs[component.id])
            findings = [f for f in findings if f.scope_id != component.id]
            findings.extend(_evaluate_scope(combined, component.id))
    unassigned = tuple(sorted(p for p in configs if _owner(p, components) is None))
    if not components:
        findings.extend(_evaluate_scope(configs, 'project'))
    elif unassigned:
        findings.extend(_evaluate_scope({p: configs[p] for p in unassigned}, 'project', existing_only=True))
    if any(p not in directories for p in overrides or {}):
        warnings = (*warnings, 'inactive_override')
    shared = tuple(sorted({r.file for r in relations if r.source != r.target}))
    architecture = ProjectArchitecture(tuple(result_components), relations,
                                      shared_files=shared,
                                      unassigned_files=tuple(p for p in unassigned if p not in shared),
                                      warnings=tuple(sorted(set(warnings))))
    return ScanResultV2(source, tuple(sorted({p for p in paths if _base_allowed(p) or is_manifest(p)} | set(files))),
        tuple(findings), architecture=architecture, directories=tuple(sorted(directories)), override_revision=revision)


def _evaluate_scope(files, scope, existing_only=False):
    from devops_flow.diagram import aggregate_status
    from devops_flow.models import TECHNICAL_RULE_IDS
    # Parseurs existants inchangés ; restauration des chemins après évaluation isolée.
    grouped = {rule: [] for rule in TECHNICAL_RULE_IDS}
    for path, content in sorted(files.items()):
        name = path.rsplit('/', 1)[-1]
        for f in evaluate_files({name: content}):
            relevant = ((name == 'Dockerfile' and f.rule_id.startswith('DOC-'))
                or (name == '.gitlab-ci.yml' and f.rule_id.startswith('CI-'))
                or (name not in {'Dockerfile', '.gitlab-ci.yml'} and f.rule_id == 'CMP-001'))
            if relevant:
                grouped[f.rule_id].append(replace(f, evidence=tuple(replace(e, file=path) for e in f.evidence)))
    empty = {f.rule_id: f for f in evaluate_files({})}
    output = []
    for rule, variants in grouped.items():
        if not variants:
            if existing_only:
                continue
            f = empty[rule]
        else:
            status = aggregate_status(f.status for f in variants)
            f = next(f for f in variants if f.status == status)
            f = replace(f, evidence=tuple(dict.fromkeys(e for v in variants for e in v.evidence)))
        f = replace(f, explanation=f.explanation.replace('à la racine supportée', 'dans ce périmètre'))
        output.append(ScopedTechnicalFinding(**f.__dict__, scope_id=scope))
    return output


def scan_project_zip(data, *, limits=DEFAULT_LIMITS, overrides=None, override_revision=0):
    if type(override_revision) is not int or override_revision < 0 or (overrides is not None and not isinstance(overrides, dict)):
        raise ScanError('override_input')
    if not isinstance(data, bytes) or len(data) > limits.archive_bytes:
        raise ScanError('archive_budget')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.entries:
                raise ScanError('entry_budget')
            members, tree, announced = [], _ZipPathNode(), 0
            for info in infos:
                parts = _zip_parts(info, limits)
                normalized = tuple(unicodedata.normalize('NFKC', p).casefold() for p in parts)
                if any(p in {'.', '..'} or p.endswith((' ', '.')) or any(c in p for c in '/\\:*?<>|') for p in normalized):
                    raise ScanError('zip_duplicate_or_path')
                _register_zip_path(tree, normalized, is_dir=info.is_dir())
                announced += info.file_size
                if (info.file_size > limits.file_bytes or announced > limits.announced_bytes
                        or info.file_size > limits.compression_ratio * info.compress_size):
                    raise ScanError('announced_budget')
                members.append((info, parts))
            roots = {p[0] for _, p in members}
            direct = any(len(p) == 1 and not i.is_dir() for i, p in members)
            offset = int(not direct and len(roots) == 1)
            if offset and _excluded(roots):
                raise ScanError('zip_secret_root')
            paths, directories = {}, {'.'}
            for info, parts in members:
                parts = parts[offset:]
                if not parts or _excluded(parts):
                    continue
                path = '/'.join(parts)
                relative_path(path)
                for n in range(1, len(parts)):
                    directories.add('/'.join(parts[:n]))
                if info.is_dir():
                    directories.add(path)
                else:
                    paths[path] = info
            components = _component_paths(paths, directories, overrides, limits)
            files, actual, warnings = {}, 0, []
            def read(path, entry=False):
                nonlocal actual
                info = paths[path]
                maximum = min(limits.file_bytes, limits.read_bytes - actual)
                if maximum < 0 or (entry and maximum < min(info.file_size, ENTRY_BYTES)):
                    raise ScanError('read_budget')
                with archive.open(info) as stream:
                    if entry:
                        content = _read_prefix(stream, min(maximum, ENTRY_BYTES))
                    else:
                        content = _read_bounded(stream, maximum)
                if len(content) > info.file_size:
                    raise ScanError('zip_size')
                actual += len(content)
                if not entry and len(content) != info.file_size:
                    raise ScanError('zip_size')
                if entry and (len(content) < info.file_size or len(content.splitlines()) > ENTRY_LINES):
                    warnings.append('entrypoint_truncated')
                return b'\n'.join(content.splitlines()[:ENTRY_LINES]) if entry else content
            for path in sorted(paths):
                if _base_allowed(path):
                    files[path] = read(path)
            for path in sorted(_entry_candidates(paths, components, files)):
                if path not in files:
                    files[path] = read(path, True)
            return _assemble('zip', paths, directories, files, overrides, override_revision, limits, warnings)
    except ScanError:
        raise
    except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError, zipfile.BadZipFile, zlib.error):
        raise ScanError('invalid_zip') from None


def _read_prefix(stream, maximum):
    # Une lecture de préfixe est volontairement partielle, jamais un fichier complet implicite.
    lines, remaining = [], maximum
    for _ in range(ENTRY_LINES):
        if remaining <= 0:
            break
        line = stream.readline(remaining)
        if not line:
            break
        if len(line) > remaining:
            raise ScanError('read_budget')
        lines.append(line)
        remaining -= len(line)
    return b''.join(lines)


def scan_project_local(path, *, limits=DEFAULT_LIMITS, overrides=None, override_revision=0):
    if type(override_revision) is not int or override_revision < 0 or (overrides is not None and not isinstance(overrides, dict)):
        raise ScanError('override_input')
    descriptors = []
    try:
        absolute = Path(os.fspath(path))
        if not absolute.is_absolute():
            absolute = Path.cwd() / absolute
        normalized = (unicodedata.normalize('NFKC', p).casefold() for p in absolute.parts)
        if any(p in SECRET_DIRECTORIES or p.startswith('.env.') for p in normalized):
            raise ScanError('local_path')
        root = _open_root(path)
        descriptors.append(root)
        paths, directories, count, directory_states = {}, {'.'}, 0, {}
        path_tree = _ZipPathNode()
        def visit(directory, descriptor):
            nonlocal count
            directory_states[descriptor] = os.fstat(descriptor)
            entries = []
            with os.scandir(descriptor) as iterator:
                for entry in iterator:
                    count += 1
                    if count > limits.entries:
                        raise ScanError('entry_budget')
                    entries.append(entry.name)
            for name in sorted(entries):
                if _excluded((name,)):
                    continue
                relative = name if directory == '.' else directory + '/' + name
                relative_path(relative)
                if len(relative.encode()) > limits.zip_path_bytes or len(relative.split('/')) > limits.zip_path_depth:
                    raise ScanError('local_path_budget')
                before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                normalized = tuple(unicodedata.normalize('NFKC', p).casefold() for p in relative.split('/'))
                _register_zip_path(path_tree, normalized, is_dir=stat.S_ISDIR(before.st_mode))
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    descriptors.append(child)
                    opened = os.fstat(child)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise ScanError('local_changed_directory')
                    directories.add(relative)
                    visit(relative, child)
                    after = os.fstat(child)
                    if (opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                        raise ScanError('local_changed_directory')
                elif stat.S_ISREG(before.st_mode):
                    paths[relative] = (descriptor, name, before)
                else:
                    raise ScanError('local_special_file')
        visit('.', root)
        components = _component_paths(paths, directories, overrides, limits)
        files, actual, announced, warnings = {}, 0, 0, []
        def read(relative, entry=False):
            nonlocal actual, announced
            parent, name, before = paths[relative]
            announced += before.st_size
            if before.st_size > limits.file_bytes or announced > limits.announced_bytes:
                raise ScanError('file_budget')
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                    raise ScanError('local_changed_file')
                maximum = min(limits.file_bytes, limits.read_bytes - actual)
                if maximum < 0 or (entry and maximum < min(opened.st_size, ENTRY_BYTES)):
                    raise ScanError('read_budget')
                content = _read_prefix(stream, min(maximum, ENTRY_BYTES)) if entry else _read_bounded(stream, maximum)
                after = os.fstat(stream.fileno())
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ScanError('local_changed_file')
            actual += len(content)
            if entry and (len(content) < opened.st_size or len(content.splitlines()) > ENTRY_LINES):
                warnings.append('entrypoint_truncated')
            return b'\n'.join(content.splitlines()[:ENTRY_LINES]) if entry else content
        for relative in sorted(paths):
            if _base_allowed(relative):
                files[relative] = read(relative)
        for relative in sorted(_entry_candidates(paths, components, files)):
            if relative not in files:
                files[relative] = read(relative, True)
        for descriptor, before in directory_states.items():
            after = os.fstat(descriptor)
            if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                raise ScanError('local_changed_directory')
        return _assemble('local', paths, directories, files, overrides, override_revision, limits, warnings)
    except ScanError:
        raise
    except (OSError, ValueError, TypeError):
        raise ScanError('local_unavailable') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
