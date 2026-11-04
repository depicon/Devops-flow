import io
import json
from dataclasses import replace
import socket
import time
import zipfile

import pytest
import requests

from devops_flow.remote import (RemoteRepoRef, RemoteFile, RemoteMetadata, RemoteError, Transport,
    GitHubConnector, GitLabConnector, TARGET_NAMES, analyze_remote, parse_repository, _public_addresses,
    _SDKSession, _PublicConnection)
from devops_flow.scanner import DEFAULT_LIMITS, ScanError, scan_local

S = 'a' * 40
REF = RemoteRepoRef('gitlab', 'gitlab.com', 'group/project', 'main')
FILES = {'Dockerfile': b'FROM alpine:latest\n', '.gitlab-ci.yml': b'test: {script: pytest}\n',
         'compose.yaml': b'services: {db: {image: postgres:17}}\n', 'pyproject.toml': b'[project]\nname="secret-package"'}


def zip_data(files=FILES):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, content in files.items():
            archive.writestr('repo-' + S + '/' + name, content)
    return out.getvalue()


class Connector:
    warnings = ()
    def __init__(self, error=None):
        self.error, self.calls = error, []
    def fetch_metadata(self, ref):
        self.calls.append(('metadata', ref))
        return RemoteMetadata('main', S, '2026-11-02', (), ())
    def fetch_files(self, ref, paths):
        self.calls.append(('files', ref))
        assert paths == TARGET_NAMES
        if self.error:
            raise RemoteError(self.error)
        return tuple(RemoteFile(k, v, len(v)) for k, v in FILES.items())
    def fetch_archive(self, ref, max_bytes):
        self.calls.append(('archive', ref))
        assert max_bytes == 10 * 1024 * 1024
        return zip_data()


@pytest.mark.parametrize('fallback', [False, True])
def test_remote_local_parity_pinned_sha_and_no_raw_provenance(tmp_path, fallback):
    for name, data in FILES.items():
        (tmp_path / name).write_bytes(data)
    connector = Connector('temporary' if fallback else None)
    result = analyze_remote(REF, connector=connector)
    local = scan_local(tmp_path)
    assert result.scan.findings == local.findings
    assert result.scan.files == local.files
    assert result.stack == ('Python déclaré', 'Projet Python (pyproject.toml)')
    assert result.method == ('archive' if fallback else 'api')
    assert all(ref.ref == S for kind, ref in connector.calls if kind != 'metadata')
    assert 'secret-package' not in json.dumps(result.provenance())


@pytest.mark.parametrize('code', ['auth', 'rate', 'destination', 'unsafe', 'budget', 'missing'])
def test_no_fallback_on_permanent_errors(code):
    c = Connector(code)
    with pytest.raises(RemoteError) as err:
        analyze_remote(REF, connector=c)
    assert err.value.code == code
    assert not any(kind == 'archive' for kind, _ in c.calls)


@pytest.mark.parametrize('url', ['http://gitlab.com/a/b', 'https://user:token@gitlab.com/a/b',
    'https://gitlab.com/a/b?token=x', 'https://gitlab.com/a/../b', 'https://gitlab.com:8501/a/b',
    'https://gitlab.com/a/%2eenv', 'https://gitlab.com/a/b/-/tree/main', 'file:///a/b', 'https://127.0.0.1/a/b#secret'])
def test_reject_ambiguous_reference(url):
    with pytest.raises(RemoteError):
        parse_repository('gitlab', url)


def test_normalization_and_subgroups():
    assert parse_repository('gitlab', 'https://gitlab.com/team/sub/repo.git', 'feature/a').project_path == 'team/sub/repo'
    assert parse_repository('github', 'https://github.com/owner/repo.git').ref == ''
    with pytest.raises(RemoteError):
        parse_repository('github', 'https://github.com/team/sub/repo')


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fe80::1'])
def test_private_destinations_rejected(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',(ip,443))])
    with pytest.raises(RemoteError) as err:
        _public_addresses('private.example')
    assert err.value.code == 'destination'


def test_connection_uses_validated_ip_without_second_dns(monkeypatch):
    calls = []
    monkeypatch.setattr('devops_flow.remote._public_addresses', lambda *a: ('8.8.8.8',))
    monkeypatch.setattr('devops_flow.remote.create_connection', lambda addr, *a, **k: calls.append(addr) or object())
    connection = _PublicConnection('gitlab.com', timeout=3)
    connection._new_conn()
    assert connection.host == 'gitlab.com' and calls == [('8.8.8.8', 443)]


class Response:
    def __init__(self, status=200, body=b'{}', headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
    def __enter__(self):return self
    def __exit__(self, *a):pass
    def iter_content(self, size):yield self.body


class Session:
    def __init__(self, *responses):self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):raise item
        return item
    def close(self):pass


@pytest.mark.parametrize('status,code', [(401,'auth'), (403,'auth'), (404,'missing'), (429,'rate'), (503,'temporary')])
def test_transport_status_redaction(status, code):
    t = Transport(REF, 'PRIVATE_TOKEN', session=Session(Response(status, b'PRIVATE_TOKEN')))
    with pytest.raises(RemoteError) as err:t.get('https://gitlab.com/api/v4/projects/x')
    assert err.value.code == code and 'PRIVATE_TOKEN' not in str(err.value)


def test_transport_bounded_raw_and_credential_safe_redirect():
    ref = replace(REF, provider='github', host='github.com')
    session = Session(Response(302, headers={'Location':'https://codeload.github.com/x/zip/main'}), Response(body=b'ZIP'))
    t = Transport(ref, 'PRIVATE_TOKEN', session=session)
    assert t.get('https://api.github.com/repos/a/b/zipball/main', archive=True, raw=True) == b'ZIP'
    assert session.calls[0][1]['headers']['Authorization'] == 'Bearer PRIVATE_TOKEN'
    assert 'Authorization' not in session.calls[1][1]['headers']
    assert session.calls[0][1]['allow_redirects'] is False
    assert session.trust_env is False


@pytest.mark.parametrize('response', [Response(body=b'12345'), Response(headers={'Content-Length':'5'})])
def test_transport_byte_limit(response):
    with pytest.raises(RemoteError) as err:
        Transport(REF, session=Session(response)).get('https://gitlab.com/x', limit=4, raw=True)
    assert err.value.code == 'budget'


@pytest.mark.parametrize('kind', ['requests', 'deadline', 'redirect', 'tls'])
def test_transport_refuses_unbounded_or_unsafe_operation(kind):
    t = Transport(REF, session=Session(Response(302, headers={'Location':'https://evil.example/steal'})))
    if kind == 'requests':t.requests = 40
    if kind == 'deadline':t.deadline = time.monotonic() - 1
    if kind == 'tls':t.session = Session(requests.exceptions.SSLError('PRIVATE_TOKEN'))
    with pytest.raises(RemoteError):t.get('https://gitlab.com/x')


class Api:
    def __init__(self, provider='github', special=False, denied_ci=False):
        self.calls, self.special, self.denied_ci, self.provider = [], special, denied_ci, provider
    def get(self, url, **kw):
        self.calls.append((url, kw))
        if url.endswith('/repository/tree') or '/git/trees/' in url:
            result = [{'path':k, 'type':'blob', 'mode':'120000' if self.special else '100644', 'sha':S, 'size':len(v)}
                      for k,v in {'Dockerfile':FILES['Dockerfile']}.items()]
            return {'tree':result, 'truncated':False} if self.provider == 'github' else result
        if '/git/blobs/' in url or '/repository/files/' in url:return FILES['Dockerfile']
        if '/actions/' in url or '/pipelines' in url:
            if self.denied_ci:raise RemoteError('auth')
            if url.endswith('/timing'):return {'run_duration_ms':12345}
            if url.endswith('/pipelines/7'):return {'id':7,'status':'success','ref':'main','duration':12.345}
            return {'workflow_runs':[{'id':7,'status':'completed','conclusion':'success','head_branch':'main'}]} if self.provider == 'github' else [{'id':7}]
        if self.provider == 'github':
            commit = {'sha':S, 'commit':{'author':{'name':'Auteur', 'date':'2026-11-02'}, 'committer':{'date':'2026-11-02'}, 'message':'Message\nIgnored'}}
        else:commit = {'id':S,'author_name':'Auteur','committed_date':'2026-11-02','title':'Message'}
        if url.endswith('/commits'):return [commit]
        if '/commits/' in url:return commit
        return {'default_branch':'main'}


@pytest.mark.parametrize('provider', ['github','gitlab'])
def test_real_connector_contract_and_raw_files(provider):
    ref = replace(REF, provider=provider, host=provider + '.com')
    api = Api(provider)
    connector = (GitHubConnector if provider == 'github' else GitLabConnector)(ref, transport=api)
    metadata = connector.fetch_metadata(ref)
    assert metadata.last_commit_sha == S
    assert metadata.pipelines[0]['duration'] == 12.345
    assert metadata.recent_commits[0]['message'] == 'Message'
    files = connector.fetch_files(replace(ref, ref=S), TARGET_NAMES)
    assert files[0].content == FILES['Dockerfile']
    assert all('ref' not in kw.get('params', {}) or kw['params']['ref'] == S for url,kw in api.calls)


@pytest.mark.parametrize('provider', ['github','gitlab'])
def test_symlinks_and_unavailable_ci(provider):
    ref = replace(REF, provider=provider, host=provider + '.com')
    api = Api(provider, special=True, denied_ci=True)
    c = (GitHubConnector if provider == 'github' else GitLabConnector)(ref, transport=api)
    assert c.fetch_metadata(ref).pipelines == () and c.warnings
    with pytest.raises(RemoteError) as err:c.fetch_files(replace(ref, ref=S), TARGET_NAMES)
    assert err.value.code == 'unsafe'
    assert not any('/git/blobs/' in url or '/repository/files/' in url for url,_ in api.calls)


def test_sdk_rejects_write_methods():
    with pytest.raises(RemoteError):_SDKSession(Api()).request('POST', 'https://gitlab.com/x')


def test_archive_traversal_rejected_before_manifest_read():
    c = Connector('temporary')
    c.fetch_archive = lambda *a: zip_data({'../pyproject.toml':b'bad'})
    with pytest.raises(ScanError):analyze_remote(REF, connector=c)


def test_token_reflected_in_commit_message_is_redacted():
    c = Connector()
    c.fetch_metadata = lambda ref: RemoteMetadata('main',S,None,({'sha':S,'message':'PRIVATE_TOKEN'},),())
    result = analyze_remote(REF, 'PRIVATE_TOKEN', connector=c)
    assert 'PRIVATE_TOKEN' not in json.dumps(result.provenance())
