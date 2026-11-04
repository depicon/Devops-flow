"""Transport HTTP réellement borné, sans spool ni extraction."""
import asyncio
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from devops_flow.ui.upload import MAX_FILE_BYTES, MAX_REQUEST_BYTES, receive_zip


def multipart(data, *, filename='test.zip', suffix=None):
    head = (f'--boundary\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/zip\r\n\r\n').encode()
    return head + data + (b'\r\n--boundary--\r\n' if suffix is None else suffix)


def request(body, *, length=None, content_type='multipart/form-data; boundary=boundary', chunks=65536):
    headers = [(b'content-type', content_type.encode())]
    if length is not None:
        headers.append((b'content-length', str(length).encode()))
    iterator = iter(range(0, len(body), chunks))
    async def receive():
        try:
            start = next(iterator)
        except StopIteration:
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        return {'type': 'http.request', 'body': body[start:start+chunks], 'more_body': start+chunks<len(body)}
    return Request({'type':'http', 'method':'POST', 'path':'/', 'headers':headers}, receive)


@pytest.mark.parametrize('size', [0, MAX_FILE_BYTES])
async def test_accepts_file_at_boundary_without_temporary_file(size):
    body = multipart(b'x'*size)
    def forbidden(*a, **k):
        pytest.fail('Un ZIP ne doit pas être spoolé dans un fichier')
    with patch('tempfile.TemporaryFile', forbidden), patch('tempfile.SpooledTemporaryFile', forbidden), patch('tempfile.NamedTemporaryFile', forbidden):
        result = await receive_zip(request(body))
    assert result.size() == size
    assert await result.read() == b'x'*size


@pytest.mark.parametrize('length', [None, 1])
async def test_server_rejects_oversized_file_even_without_honest_length(length):
    with pytest.raises(HTTPException) as caught:
        await receive_zip(request(multipart(b'x'*(MAX_FILE_BYTES+1)), length=length))
    assert caught.value.status_code == 413


@pytest.mark.parametrize('case', ['request_length','actual_length','multiple','field','truncated','extension','header','type','boundary'])
async def test_rejects_invalid_multipart_and_budget_overflow(case):
    body, kwargs = multipart(b'abc'), {}
    if case == 'request_length':kwargs['length'] = MAX_REQUEST_BYTES+1
    if case == 'actual_length':body = multipart(b'x'*(MAX_REQUEST_BYTES+1))
    if case == 'multiple':body = multipart(b'a',suffix=b'\r\n') + multipart(b'b')
    if case == 'field':body = b'--boundary\r\nContent-Disposition: form-data; name="field"\r\n\r\na\r\n--boundary--\r\n'
    if case == 'truncated':body = multipart(b'a',suffix=b'')
    if case == 'extension':body = multipart(b'a',filename='test.txt')
    if case == 'header':body = multipart(b'a',filename='x'*20000+'.zip')
    if case == 'type':kwargs['content_type'] = 'text/plain'
    if case == 'boundary':kwargs['content_type'] = 'multipart/form-data'
    with pytest.raises(HTTPException):
        await receive_zip(request(body, **kwargs))


async def test_native_route_is_replaced_and_http_rejection_invalidates_old_scan(gui, store):
    from nicegui import app
    from pathlib import Path
    p = store.create_project('Route contrôlée')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, Path(__file__).parent/'fixtures/healthy_project')
    await gui.set(f'scan_mode_{p.id}', 'Archive ZIP')
    upload = gui.element(f'scan_zip_{p.id}')
    assert len([r for r in app.routes if getattr(r, 'path', '') == upload._registered_url]) == 1
    response = await gui.user.http_client.post(upload._registered_url, content=b'', headers={'Content-Length':str(MAX_REQUEST_BYTES+1)})
    assert response.status_code == 413
    assert gui.session.project().scan is None and gui.session.project().pending_zip is None
    await gui.user.should_see('Archive refusée')


async def test_disabled_upload_does_not_accept_files(gui, store):
    p = store.create_project('Import désactivé')
    await gui.open(p.id, 'Diagnostic guidé')
    upload = gui.element(f'scan_zip_{p.id}')
    upload.disable()
    response = await gui.user.http_client.post(upload._registered_url, files={'file':('x.zip', b'abc')})
    assert response.status_code == 403
    assert gui.session.project().pending_zip is None


async def test_removing_pending_archive_releases_bytes_without_false_error(gui, store):
    p = store.create_project('Archive retirée')
    await gui.open(p.id, 'Diagnostic guidé')
    assert (await gui.upload(p.id, b'archive fictive')).status_code == 200
    assert gui.session.project().pending_zip == b'archive fictive'
    await gui.click(f'scan_clear_{p.id}')
    assert gui.session.project().pending_zip is None
    assert gui.session.project().scan is None
    assert gui.shell.feedback is None
    await gui.user.should_not_see('Archive refusée')


async def test_late_upload_cannot_overwrite_newer_archive(gui, store):
    p = store.create_project('Deux imports')
    await gui.open(p.id, 'Diagnostic guidé')
    path = gui.element(f'scan_zip_{p.id}')._registered_url
    started, release = asyncio.Event(), asyncio.Event()
    body = multipart(b'ancienne archive')
    async def slow_body():
        yield body[:20]
        started.set()
        await release.wait()
        yield body[20:]
    first = asyncio.create_task(gui.user.http_client.post(path, content=slow_body(), headers={'Content-Type':'multipart/form-data; boundary=boundary'}))
    await started.wait()
    second = await gui.user.http_client.post(path, files={'file':('nouveau.zip',b'nouvelle archive')})
    release.set()
    old = await first
    await gui.idle()
    assert second.status_code == 200
    assert old.status_code in (200,409)
    assert gui.session.project().pending_zip == b'nouvelle archive'
