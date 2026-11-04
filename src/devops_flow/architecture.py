"""Vue DevOps immuable et chemins relatifs portables, sans accès au dépôt."""
from dataclasses import asdict, dataclass
import hashlib
import re
import unicodedata

from devops_flow.signals import Signal

ARCHITECTURE_VERSION = 'architecture-v1'
DEVOPS_NAMES = frozenset({'Dockerfile', 'compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml', '.gitlab-ci.yml'})
EXCLUDED_DIRECTORIES = frozenset({'.git', '.env', '.ssh', '.aws', '.gnupg', 'secrets',
    '.venv', 'venv', 'node_modules', 'vendor', '.tox', '__pycache__', '.next', 'dist',
    'build', 'target', '.gradle', 'coverage', '.pytest_cache', '.mypy_cache', '.ruff_cache'})
MANIFEST_NAMES = frozenset({'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt',
    'Pipfile', 'package.json', 'go.mod', 'pom.xml', 'build.gradle', 'build.gradle.kts',
    'Cargo.toml', 'Gemfile', 'composer.json', 'mix.exs'})


def relative_path(path: str, *, root=False) -> str:
    if root and path == '.':
        return path
    if not isinstance(path, str) or not path or len(path.encode('utf-8')) > 1024:
        raise ValueError('Chemin relatif invalide.')
    parts = path.split('/')
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    if len(parts) > 32 or any(not p or p in {'.', '..'} or p.endswith((' ', '.'))
        or p.split('.')[0].casefold() in reserved
        or any(unicodedata.category(c).startswith('C') or c in '\\:*?<>|' for c in p)
        or unicodedata.normalize('NFKC', p).casefold() in EXCLUDED_DIRECTORIES | {'.', '..'}
        or unicodedata.normalize('NFKC', p).casefold().startswith('.env.')
        or any(c in '/\\:*?<>|' for c in unicodedata.normalize('NFKC', p)) for p in parts):
        raise ValueError('Chemin relatif invalide.')
    return path


def component_id(path: str) -> str:
    return 'c_' + hashlib.sha256(relative_path(path, root=True).encode()).hexdigest()[:24]


def is_manifest(path: str) -> bool:
    name = path.rsplit('/', 1)[-1]
    return name in MANIFEST_NAMES or bool(re.fullmatch(r'[^/]+\.csproj', name))


@dataclass(frozen=True)
class ComponentDevOpsState:
    files: tuple[str, ...] = ()
    linked_files: tuple[str, ...] = ()
    potential_missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class Component:
    id: str
    path: str
    manifests: tuple[str, ...] = ()
    signals: tuple[Signal, ...] = ()
    devops: ComponentDevOpsState = ComponentDevOpsState()
    declared: bool = False

    @property
    def effective_signals(self):
        manual = {s.field for s in self.signals if s.confidence == 'manual'}
        return tuple(s for s in self.signals if s.confidence == 'manual' or s.field not in manual)


@dataclass(frozen=True)
class ArchitectureRelation:
    source: str
    target: str
    kind: str
    file: str
    confidence: str = 'high'


@dataclass(frozen=True)
class ProjectArchitecture:
    components: tuple[Component, ...] = ()
    relations: tuple[ArchitectureRelation, ...] = ()
    unassigned_files: tuple[str, ...] = ()
    shared_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    version: str = ARCHITECTURE_VERSION

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        try:
            if data['version'] != ARCHITECTURE_VERSION or len(data['components']) > 20:
                raise ValueError
            components = []
            for c in data['components']:
                path = relative_path(c['path'], root=True)
                if c['id'] != component_id(path) or type(c['declared']) is not bool:
                    raise ValueError
                for p in (*c['manifests'], *c['devops']['files'], *c['devops']['linked_files']):
                    relative_path(p)
                if any(not is_manifest(p) for p in c['manifests']) or any(p.rsplit('/', 1)[-1] not in DEVOPS_NAMES for p in (*c['devops']['files'], *c['devops']['linked_files'])):
                    raise ValueError
                if len(c['signals']) > 272:
                    raise ValueError
                signals = tuple(Signal(**s) for s in c['signals'])
                for s in signals:
                    if s.file is not None:
                        relative_path(s.file)
                    if s.field == 'entrypoint' and s.value is not None:
                        relative_path(s.value)
                devops = ComponentDevOpsState(**{k: tuple(v) for k, v in c['devops'].items()})
                if any(p not in {'Dockerfile', '.gitlab-ci.yml'} for p in devops.potential_missing):
                    raise ValueError
                components.append(Component(c['id'], path, tuple(c['manifests']), signals, devops, c['declared']))
            ids = {c.id for c in components}
            if len(ids) != len(components):
                raise ValueError
            relations = tuple(ArchitectureRelation(**r) for r in data['relations'])
            for r in relations:
                relative_path(r.file)
                if (r.source not in ids | {'project'} or r.target not in ids
                        or r.kind not in {'compose_build', 'ci_include', 'compose_dependency'}
                        or r.confidence not in {'high', 'medium', 'low'} or r.file.rsplit('/', 1)[-1] not in DEVOPS_NAMES):
                    raise ValueError
            for path in (*data['unassigned_files'], *data['shared_files']):
                relative_path(path)
                if path.rsplit('/', 1)[-1] not in DEVOPS_NAMES:
                    raise ValueError
            if any(w not in {'invalid_manifest', 'unresolved_manifest', 'inactive_override', 'conflicting_signals', 'entrypoint_truncated'} for w in data['warnings']):
                raise ValueError
            return cls(tuple(components), relations, tuple(data['unassigned_files']),
                       tuple(data['shared_files']), tuple(data['warnings']))
        except (KeyError, TypeError, AttributeError):
            raise ValueError('Architecture enregistrée invalide.') from None
