"""Réception multipart bornée en mémoire ; aucun fichier temporaire."""

from fastapi import HTTPException, Request
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header

from devops_flow.scanner import DEFAULT_LIMITS

MAX_FILE_BYTES = DEFAULT_LIMITS.archive_bytes
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 64 * 1024
REJECTED = "Archive refusée : choisissez un seul ZIP de 10 Mio maximum."


async def receive_zip(request: Request, *, limit=MAX_FILE_BYTES, extension='.zip', rejected=REJECTED) -> SmallFileUpload:
    """Valider les limites pendant la lecture, avant tout parsing de ZIP."""
    try:
        request_limit = limit + 64 * 1024
        length = request.headers.get("content-length")
        if length is not None and (int(length) < 0 or int(length) > request_limit):
            raise HTTPException(413, rejected)
        kind, options = parse_options_header(request.headers.get("content-type", ""))
        boundary = options.get(b"boundary", b"")
        if kind != b"multipart/form-data" or not 1 <= len(boundary) <= 70:
            raise HTTPException(400, rejected)
        payload = bytearray()
        headers = {}
        header_field, header_value = bytearray(), bytearray()
        count, header_bytes = 0, 0
        ended, part_ended = False, False
        filename = ""

        def begin():
            nonlocal count
            count += 1
            if count != 1:
                raise HTTPException(400, rejected)

        def header_data(buffer, data, start, end):
            nonlocal header_bytes
            header_bytes += end - start
            if header_bytes > 16 * 1024:
                raise HTTPException(400, rejected)
            buffer.extend(data[start:end])

        def header_end():
            key = bytes(header_field).lower()
            if key in headers:
                raise HTTPException(400, rejected)
            headers[key] = bytes(header_value)
            header_field.clear()
            header_value.clear()

        def headers_finished():
            nonlocal filename
            disposition, params = parse_options_header(headers.get(b"content-disposition", b""))
            name = params.get(b"filename")
            if disposition != b"form-data" or name is None:
                raise HTTPException(400, rejected)
            filename = name.decode("utf-8", errors="replace").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if not filename.lower().endswith(extension):
                raise HTTPException(400, rejected)

        def data_received(data, start, end):
            if len(payload) + end - start > limit:
                raise HTTPException(413, rejected)
            payload.extend(data[start:end])

        def end_part():
            nonlocal part_ended
            part_ended = True

        def end():
            nonlocal ended
            ended = True

        parser = MultipartParser(boundary, {
            "on_part_begin": begin,
            "on_header_field": lambda d, s, e: header_data(header_field, d, s, e),
            "on_header_value": lambda d, s, e: header_data(header_value, d, s, e),
            "on_header_end": header_end, "on_headers_finished": headers_finished,
            "on_part_data": data_received, "on_part_end": end_part, "on_end": end,
        })
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > request_limit:
                raise HTTPException(413, rejected)
            parser.write(chunk)
        parser.finalize()
        if count != 1 or not ended or not part_ended or not filename:
            raise HTTPException(400, rejected)
        return SmallFileUpload(filename, 'text/markdown' if extension == '.md' else 'application/zip', bytes(payload))
    except HTTPException:
        raise
    except (ValueError, TypeError):
        raise HTTPException(400, rejected) from None


class MemoryUpload(ui.upload):
    """Adapter la route NiceGUI 3.18 épinglée, sans modifier son code installé."""

    def __init__(self, *, on_start, on_file, on_error, file_limit=MAX_FILE_BYTES,
                 extension='.zip', rejected_message=REJECTED, **kwargs):
        super().__init__(max_files=1, max_file_size=file_limit,
                         max_total_size=file_limit, **kwargs)
        app.remove_route(self._registered_url)

        @app.post(self._registered_url, include_in_schema=False)
        async def upload_route(request: Request):
            if self.is_ignoring_events:
                raise HTTPException(403, "Import indisponible.")
            ticket = on_start()
            try:
                uploaded = await receive_zip(request, limit=file_limit, extension=extension, rejected=rejected_message)
                if self.is_ignoring_events:
                    raise HTTPException(409, "Source modifiée. Sélectionnez à nouveau le ZIP.")
                await on_file(uploaded, ticket)
            except HTTPException:
                on_error(ticket)
                raise
            return {"upload": "success"}
