"""HTTP helpers for session-file upload/download. Knobs from Config."""
from __future__ import annotations

import re
from typing import Tuple
from urllib.parse import quote

from .errors import MailboxError
from . import settings

_ASCII_NAME_RE = re.compile(r"[^\x20-\x7e]+", re.ASCII)


async def read_multipart_upload(request, config) -> Tuple[str, str, bytes]:
    form = await request.form()
    upload = form.get(settings.upload_form_field(config))
    reader = getattr(upload, "read", None)
    if upload is None or not callable(reader):
        raise MailboxError(settings.missing_upload_message(config))
    data = await reader()
    filename = str(
        form.get(settings.upload_filename_field(config))
        or getattr(upload, "filename", None)
        or ""
    )
    mime = str(
        form.get(settings.upload_mime_field(config))
        or getattr(upload, "content_type", None)
        or ""
    )
    return filename, mime, data or b""


def download_disposition_header(request, config, filename: str) -> str:
    param = settings.inline_query_param(config)
    raw = request.query_params.get(param) or ""
    kind = settings.download_disposition(config)
    if settings.query_is_truthy(config, raw):
        kind = settings.inline_disposition(config)
    fallback = settings.fallback_filename(config)
    name = (filename or fallback).replace('"', "")

    # RFC 5987: Starlette encodes header values as latin-1. Keep an ASCII
    # `filename=` fallback and carry the real name in `filename*=UTF-8''…`.
    ascii_fallback = _ASCII_NAME_RE.sub("_", fallback).strip() or "file"
    ascii_name = _ASCII_NAME_RE.sub("_", name).strip() or ascii_fallback
    ascii_name = ascii_name.replace('"', "")
    encoded = quote(name, safe="")

    header = f'{kind}; filename="{ascii_name}"'
    if encoded != ascii_name:
        header += f"; filename*=UTF-8''{encoded}"
    return header
