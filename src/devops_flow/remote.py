"""Acquisition REST bornée en lecture seule. Aucun clone, disque ou import GUI."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import io
import ipaddress
import json
import re
import socket
import time
from typing import Literal, Protocol
from urllib.parse import quote, urljoin, urlsplit
import zipfile

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.util.connection import create_connection

from devops_flow.scanner import ALLOWED_NAMES, DEFAULT_LIMITS, ScanResult, scan_zip

Provider = Literal['gitlab', 'github']
MANIFEST_NAMES = ('pyproject.toml', 'requirements.txt', 'package.json', 'go.mod', '.python-version', '.nvmrc')
TARGET_NAMES = ALLOWED_NAMES + MANIFEST_NAMES
SHA = re.compile(r'^[0-9a-fA-F]{40,64}$')
MESSAGES = {
    'reference': 'Référence distante invalide : utilisez une URL HTTPS de dépôt et une référence simple.',
    'network': 'Connexion distante impossible. Vérifiez l’hôte et réessayez.',
    'destination': 'Destination refusée : seul un hôte HTTPS public avec TLS vérifié est autorisé.',
    'auth': 'Accès refusé. Vérifiez le jeton et ses permissions de lecture.',
    'missing': 'Dépôt ou référence inaccessible. Vérifiez l’URL, la référence et les permissions.',
    'rate': 'Limite de requêtes atteinte. Réessayez plus tard.',
    'temporary': 'API temporairement indisponible. Réessayez plus tard.',
    'budget': 'Acquisition refusée : limite de temps, requêtes ou taille dépassée.',
    'unsafe': 'Acquisition refusée : fichier spécial ou réponse distante non sûre.',
}


class RemoteError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES.get(code, MESSAGES['unsafe']))


@dataclass(frozen=True)
class RemoteRepoRef:
    provider: Provider
    host: str
    project_path: str
    ref: str


@dataclass(frozen=True)
class RemoteFile:
    path: str
    content: bytes
    size: int


@dataclass(frozen=True)
class RemoteMetadata:
    default_branch: str
    last_commit_sha: str | None
    last_commit_at: str | None
    recent_commits: tuple[dict, ...]
    pipelines: tuple[dict, ...]


class RemoteConnector(Protocol):
    def fetch_metadata(self, ref: RemoteRepoRef) -> RemoteMetadata: ...
    def fetch_files(self, ref: RemoteRepoRef, paths: tuple[str, ...]) -> tuple[RemoteFile, ...]: ...
    def fetch_archive(self, ref: RemoteRepoRef, max_bytes: int) -> bytes: ...


@dataclass(frozen=True)
class RemoteAcquisition:
    reference: RemoteRepoRef
    metadata: RemoteMetadata
    scan: ScanResult
    stack: tuple[str, ...]
    method: str
    warnings: tuple[str, ...] = ()

    def provenance(self):
        return {'reference': asdict(self.reference), 'metadata': asdict(self.metadata),
                'stack': list(self.stack), 'method': self.method, 'warnings': list(self.warnings)}


def _text(value, maximum):
    return ''.join(c for c in str(value or '') if c.isprintable())[:maximum]


def parse_repository(provider: Provider, url: str, ref: str = '') -> RemoteRepoRef:
    try:
        p = urlsplit(url.strip())
        if (provider not in ('gitlab', 'github') or p.scheme != 'https' or not p.hostname
                or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment):
            raise ValueError
        host = p.hostname.encode('idna').decode('ascii').lower()
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or '..' in host:
            raise ValueError
        parts = p.path.strip('/').removesuffix('.git').split('/')
        if (len(parts) < 2 or (provider == 'github' and len(parts) != 2)
                or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', s) or s in ('.', '..', '-') for s in parts)):
            raise ValueError
        branch = ref.strip()
        if (len(branch) > 255 or any(ord(c) < 32 for c in branch) or any(c in branch for c in '?&#\\')
                or '..' in branch or branch.startswith('/')):
            raise ValueError
        return RemoteRepoRef(provider, host, '/'.join(parts), branch)
    except (ValueError, UnicodeError, TypeError, AttributeError):
        raise RemoteError('reference') from None


def _public_addresses(host, port=443):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = tuple(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise RemoteError('destination')
        return ips
    except (OSError, ValueError):
        raise RemoteError('destination') from None


class _PublicConnection(HTTPSConnection):
    def _new_conn(self):
        # Résolution et connexion au même IP validé : pas de seconde résolution DNS.
        address = _public_addresses(self.host, self.port)[0]
        return create_connection((address, self.port), self.timeout,
                                 source_address=self.source_address, socket_options=self.socket_options)


class _PublicPool(HTTPSConnectionPool):
    ConnectionCls = _PublicConnection


class _PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = dict(self.poolmanager.pool_classes_by_scheme, https=_PublicPool)


class Transport:
    """GET bornés, redirections explicites, sans proxy/netrc ni exceptions brutes."""
    def __init__(self, ref, token='', *, session=None):
        self.origin = 'api.github.com' if ref.provider == 'github' and ref.host == 'github.com' else ref.host
        self.archive_origin = 'codeload.github.com' if ref.host == 'github.com' else self.origin
        self.token = token
        self.provider = ref.provider
        self.deadline = time.monotonic() + 60
        self.phase_deadline = self.deadline
        self.requests = 0
        self.session = session or requests.Session()
        self.session.trust_env = False
        if session is None:
            self.session.mount('https://', _PublicAdapter(max_retries=0))

    def close(self):
        self.token = ''
        self.session.close()

    def get(self, url, *, params=None, limit=1024 * 1024, raw=False, archive=False):
        for _ in range(4):
            p = urlsplit(url)
            if (p.scheme != 'https' or p.hostname not in {self.origin, self.archive_origin if archive else self.origin}
                    or p.port not in (None, 443) or p.username or p.password or p.fragment):
                raise RemoteError('destination')
            remaining = min(self.deadline, self.phase_deadline) - time.monotonic()
            self.requests += 1
            if remaining <= 0:
                raise RemoteError('temporary' if self.phase_deadline < self.deadline else 'budget')
            if self.requests > 40:
                raise RemoteError('budget')
            headers = {'Accept': ('application/vnd.github.raw+json' if self.provider == 'github' and not archive
                                  else 'application/octet-stream') if raw else 'application/json'}
            if self.token and p.hostname == self.origin:
                headers['PRIVATE-TOKEN' if self.provider == 'gitlab' else 'Authorization'] = (
                    self.token if self.provider == 'gitlab' else 'Bearer ' + self.token)
            try:
                with self.session.get(url, params=params, headers=headers, stream=True, allow_redirects=False,
                                      verify=True, timeout=(min(3, remaining), min(5, remaining))) as response:
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get('Location', ''))
                        params = None
                        continue
                    if status == 429 or (status == 403 and response.headers.get('X-RateLimit-Remaining') == '0'):
                        raise RemoteError('rate')
                    if status in (401, 403):
                        raise RemoteError('auth')
                    if status == 404:
                        raise RemoteError('missing')
                    if status in (408, 500, 502, 503, 504):
                        raise RemoteError('temporary')
                    if status != 200:
                        raise RemoteError('unsafe')
                    announced = response.headers.get('Content-Length')
                    if announced and (not announced.isdigit() or int(announced) > limit):
                        raise RemoteError('budget')
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        if len(data) > limit:
                            raise RemoteError('budget')
                        if time.monotonic() >= min(self.deadline, self.phase_deadline):
                            raise RemoteError('temporary' if self.phase_deadline < self.deadline else 'budget')
                    if raw:
                        return bytes(data)
                    return json.loads(data)
            except RemoteError:
                raise
            except requests.exceptions.SSLError:
                raise RemoteError('destination') from None
            except requests.exceptions.Timeout:
                raise RemoteError('temporary') from None
            except requests.exceptions.RequestException:
                raise RemoteError('network') from None
            except (ValueError, UnicodeError):
                raise RemoteError('unsafe') from None
        raise RemoteError('destination')


class GitHubConnector:
    def __init__(self, ref, token='', *, transport=None):
        self.transport = transport or Transport(ref, token)
        base = 'https://api.github.com' if ref.host == 'github.com' else 'https://' + ref.host + '/api/v3'
        self.base = base + '/repos/' + quote(ref.project_path, safe='/')
        self.warnings = []

    def fetch_metadata(self, ref):
        repo = self.transport.get(self.base)
        default = repo['default_branch']
        commit = self.transport.get(self.base + '/commits/' + quote(ref.ref or default, safe=''))
        sha = commit['sha']
        if not SHA.fullmatch(sha):
            raise RemoteError('unsafe')
        commits = self.transport.get(self.base + '/commits', params={'sha': sha, 'per_page': 10})
        recent = tuple({'sha': _sha(c['sha']), 'author': _text(c['commit']['author']['name'], 120),
                        'date': _text(c['commit']['author']['date'], 40),
                        'message': _text(c['commit']['message'].split('\n')[0], 200)} for c in commits[:10])
        pipelines = []
        try:
            runs = self.transport.get(self.base + '/actions/runs', params={'head_sha': sha, 'per_page': 5})['workflow_runs']
            for run in runs[:5]:
                duration = None
                try:
                    usage = self.transport.get(self.base + '/actions/runs/' + str(int(run['id'])) + '/timing')
                    milliseconds = usage.get('run_duration_ms')
                    if type(milliseconds) in (int, float) and 0 <= milliseconds <= 31536000000:
                        duration = milliseconds / 1000
                except RemoteError as error:
                    if error.code not in ('auth', 'missing', 'temporary', 'rate', 'network'):
                        raise
                pipelines.append({'id': int(run['id']), 'status': _text(run.get('conclusion') or run['status'], 40),
                                  'ref': _text(run.get('head_branch'), 255), 'duration': duration})
        except RemoteError as error:
            if error.code in ('auth', 'missing', 'temporary', 'rate', 'network'):
                self.warnings.append('Historique CI indisponible ; permissions ou disponibilité à vérifier.')
            else:
                raise
        return RemoteMetadata(_text(default, 255), sha, _text(commit['commit']['committer']['date'], 40), recent, tuple(pipelines))

    def fetch_files(self, ref, paths):
        _check_paths(paths)
        tree = self.transport.get(self.base + '/git/trees/' + quote(ref.ref, safe=''))
        if tree.get('truncated'):
            raise RemoteError('budget')
        entries = tree['tree']
        if len(entries) > 1000:
            raise RemoteError('budget')
        selected = _select_tree(entries, paths)
        result = []
        total = 0
        for entry in selected:
            content = self.transport.get(self.base + '/git/blobs/' + _sha(entry['sha']), raw=True,
                                         limit=min(DEFAULT_LIMITS.file_bytes, DEFAULT_LIMITS.read_bytes - total))
            # Les blobs GitHub requièrent le type raw pour obtenir les octets exacts.
            total += len(content)
            if len(content) != entry['size']:
                raise RemoteError('unsafe')
            result.append(RemoteFile(entry['path'], content, len(content)))
        return tuple(result)

    def fetch_archive(self, ref, max_bytes):
        return self.transport.get(self.base + '/zipball/' + quote(ref.ref, safe=''),
                                  raw=True, archive=True, limit=max_bytes)


class _SDKSession(requests.Session):
    """Branche le SDK GitLab sur le même transport borné, sans deuxième accès réseau."""
    def __init__(self, transport):
        super().__init__()
        self.trust_env = False
        self.transport = transport

    def request(self, method, url, **kwargs):
        if method.upper() not in ('GET', 'HEAD') or kwargs.get('data') or kwargs.get('json'):
            raise RemoteError('unsafe')
        data = self.transport.get(url, params=kwargs.get('params'))
        response = requests.Response()
        response.status_code, response.url = 200, url
        response.headers['Content-Type'] = 'application/json'
        response._content = json.dumps(data).encode('utf-8')
        return response


class GitLabConnector:
    def __init__(self, ref, token='', *, transport=None):
        import gitlab
        self.transport = transport or Transport(ref, token)
        self.client = gitlab.Gitlab('https://' + ref.host, private_token=token or None, timeout=5,
                                    session=_SDKSession(self.transport))
        self.base = self.client.api_url + '/projects/' + quote(ref.project_path, safe='')
        self.warnings = []

    def _json(self, url, params=None):
        return self.client.http_get(url, query_data=params, max_retries=0, obey_rate_limit=False)

    def fetch_metadata(self, ref):
        repo = self._json(self.base)
        default = repo['default_branch']
        commit = self._json(self.base + '/repository/commits/' + quote(ref.ref or default, safe=''))
        sha = _sha(commit['id'])
        commits = self._json(self.base + '/repository/commits', params={'ref_name': sha, 'per_page': 10})
        recent = tuple({'sha': _sha(c['id']), 'author': _text(c['author_name'], 120),
                        'date': _text(c['committed_date'], 40), 'message': _text(c['title'], 200)} for c in commits[:10])
        pipelines = []
        try:
            for item in self._json(self.base + '/pipelines', params={'sha': sha, 'per_page': 5})[:5]:
                pipeline = self._json(self.base + '/pipelines/' + str(int(item['id'])))
                duration = pipeline.get('duration')
                pipelines.append({'id': int(pipeline['id']), 'status': _text(pipeline['status'], 40),
                                  'ref': _text(pipeline.get('ref'), 255),
                                  'duration': duration if type(duration) in (int, float) and 0 <= duration <= 31536000 else None})
        except RemoteError as error:
            if error.code in ('auth', 'missing', 'temporary', 'rate', 'network'):
                self.warnings.append('Historique CI indisponible ; permissions ou disponibilité à vérifier.')
            else:
                raise
        return RemoteMetadata(_text(default, 255), sha, _text(commit['committed_date'], 40), recent, tuple(pipelines))

    def fetch_files(self, ref, paths):
        _check_paths(paths)
        entries = []
        for page in range(1, 12):
            batch = self._json(self.base + '/repository/tree', params={'ref': ref.ref, 'per_page': 100, 'page': page})
            entries.extend(batch)
            if len(entries) > 1000:
                raise RemoteError('budget')
            if len(batch) < 100:
                break
        selected = _select_tree(entries, paths)
        result, total = [], 0
        for entry in selected:
            content = self.transport.get(self.base + '/repository/files/' + quote(entry['path'], safe='') + '/raw',
                                         params={'ref': ref.ref, 'lfs': 'false'}, raw=True,
                                         limit=min(DEFAULT_LIMITS.file_bytes, DEFAULT_LIMITS.read_bytes - total))
            total += len(content)
            result.append(RemoteFile(entry['path'], content, len(content)))
        return tuple(result)

    def fetch_archive(self, ref, max_bytes):
        return self.transport.get(self.base + '/repository/archive.zip', params={'sha': ref.ref, 'include_lfs_blobs': 'false'},
                                  raw=True, archive=True, limit=max_bytes)


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise RemoteError('unsafe')
    return value.lower()


def _check_paths(paths):
    if len(paths) != len(set(paths)) or any(p not in TARGET_NAMES for p in paths):
        raise RemoteError('unsafe')


def _select_tree(entries, paths):
    selected, seen = [], set()
    for entry in entries:
        if entry.get('path') not in paths:
            continue
        if (entry['path'] in seen or entry.get('mode') not in ('100644', '100755') or entry.get('type') != 'blob'):
            raise RemoteError('unsafe')
        if type(entry.get('size', 0)) is not int or not 0 <= entry.get('size', 0) <= DEFAULT_LIMITS.file_bytes:
            raise RemoteError('budget')
        seen.add(entry['path'])
        selected.append(entry)
    return selected


def _zip_files(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for file in files:
            if file.path in ALLOWED_NAMES:
                archive.writestr(file.path, file.content)
    return output.getvalue()


def _archive_files(data):
    # scan_zip valide TOUS les noms/métadonnées avant toute lecture de manifest.
    scan = scan_zip(data)
    manifests = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        direct = any('/' not in i.filename and not i.is_dir() for i in infos)
        offset = 0 if direct else 1
        total = sum(i.file_size for i in infos if len(i.filename.rstrip('/').split('/')) == offset + 1
                    and i.filename.rstrip('/').split('/')[-1] in ALLOWED_NAMES)
        for info in infos:
            parts = info.filename.rstrip('/').split('/')
            if info.is_dir() or len(parts) != offset + 1 or parts[-1] not in TARGET_NAMES:
                continue
            if parts[-1] in ALLOWED_NAMES:
                continue
            if total + info.file_size > DEFAULT_LIMITS.read_bytes:
                raise RemoteError('budget')
            with archive.open(info) as stream:
                content = stream.read(min(DEFAULT_LIMITS.file_bytes, DEFAULT_LIMITS.read_bytes - total) + 1)
            total += len(content)
            if len(content) != info.file_size or total > DEFAULT_LIMITS.read_bytes:
                raise RemoteError('budget')
            manifests.append(RemoteFile(parts[-1], content, len(content)))
    return scan, tuple(manifests)


def detect_stack(files):
    names = {f.path for f in files}
    hints = []
    if names & {'pyproject.toml', 'requirements.txt', '.python-version'}:
        hints.append('Python déclaré')
    if 'pyproject.toml' in names:
        hints.append('Projet Python (pyproject.toml)')
    if 'requirements.txt' in names:
        hints.append('Dépendances pip déclarées')
    if names & {'package.json', '.nvmrc'}:
        hints.append('Node.js déclaré')
    if 'package.json' in names:
        hints.append('Projet Node.js (package.json)')
    if 'go.mod' in names:
        hints.append('Go déclaré (modules)')
    return tuple(hints)


def analyze_remote(reference, token='', *, connector=None):
    if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 for c in token):
        raise RemoteError('auth')
    reference = parse_repository(reference.provider, 'https://' + reference.host + '/' + reference.project_path, reference.ref)
    owned = connector is None
    if owned:
        connector = (GitLabConnector if reference.provider == 'gitlab' else GitHubConnector)(reference, token)
    transport = getattr(connector, 'transport', None)
    try:
        metadata = connector.fetch_metadata(reference)
        if metadata.last_commit_sha is None:
            raise RemoteError('missing')
        pinned = replace(reference, ref=_sha(metadata.last_commit_sha))
        try:
            if transport:
                transport.phase_deadline = min(transport.deadline, time.monotonic() + 20)
            files = connector.fetch_files(pinned, TARGET_NAMES)
            _check_paths(tuple(f.path for f in files))
            if (sum(f.size for f in files) > DEFAULT_LIMITS.read_bytes
                    or any(f.size != len(f.content) or f.size > DEFAULT_LIMITS.file_bytes for f in files)):
                raise RemoteError('budget')
            scan, method = scan_zip(_zip_files(files)), 'api'
        except RemoteError as error:
            if error.code not in ('temporary', 'network'):
                raise
            if transport:
                transport.phase_deadline = transport.deadline
            data = connector.fetch_archive(pinned, DEFAULT_LIMITS.archive_bytes)
            scan, files = _archive_files(data)
            method = 'archive'
        if token:
            def scrub(value):
                if isinstance(value, str):
                    return value.replace(token, '[jeton masqué]')
                if isinstance(value, dict):
                    return {key: scrub(item) for key, item in value.items()}
                if isinstance(value, tuple):
                    return tuple(scrub(item) for item in value)
                return value
            metadata = RemoteMetadata(**scrub(asdict(metadata)))
        return RemoteAcquisition(reference, metadata, scan, detect_stack(files), method,
                                 tuple(getattr(connector, 'warnings', ())))
    except (KeyError, TypeError, IndexError, AttributeError):
        raise RemoteError('unsafe') from None
    finally:
        if owned:
            connector.transport.close()
            if hasattr(connector, 'client'):
                connector.client.private_token = None
                connector.client.session.close()
