"""Package generated files for the Sleuth session mailbox.

Sleuth encrypts and uploads. This module returns ``files[]`` with
``content_base64`` (plaintext). Optional https url / object_key still
registers an already-stored object without rewriting it.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict
from urllib.parse import urlparse

from .config import Settings


def _http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _safe_name(filename: str) -> str:
    name = (filename or "output.txt").strip() or "output.txt"
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.ASCII)
    return name or "output.txt"


def emit_file(
    settings: Settings,
    *,
    filename: str,
    content: str = "",
    content_bytes: bytes | None = None,
    url: str = "",
    mime: str = "text/plain",
    object_key: str = "",
    size: int = 0,
) -> Dict[str, Any]:
    del settings  # mailbox upload is Sleuth's job; keep the call signature
    name = _safe_name(filename)
    href = (url or "").strip()
    key = (object_key or "").strip()
    body = content if isinstance(content, str) else ""
    mime_s = (mime or "text/plain").strip() or "text/plain"
    if href:
        if not _http_url(href) or href.startswith("data:") or href.lower().startswith("file:"):
            return {
                "ok": False,
                "detail": "url must be http(s); data: and file: are not allowed",
                "files": [],
            }
    raw = content_bytes if content_bytes is not None else (body.encode("utf-8") if body else b"")
    if raw and not href and not key:
        encoded = base64.b64encode(raw).decode("ascii")
        return {
            "ok": True,
            "files": [
                {
                    "filename": name,
                    "mime": mime_s,
                    "size": len(raw),
                    "content_base64": encoded,
                }
            ],
        }
    if not href and not key:
        return {
            "ok": False,
            "detail": "provide content to return, or https url / object_key",
            "files": [],
        }
    entry: Dict[str, Any] = {
        "filename": name,
        "mime": mime_s,
        "size": int(size or (len(raw) if raw else 0)),
    }
    if href:
        entry["url"] = href
    if key:
        entry["object_key"] = key
    return {"ok": True, "files": [entry]}


def register(server: Any, settings: Settings) -> None:
    @server.tool(
        name="emit_file",
        description=(
            "Package a generated file for the Sleuth session mailbox. "
            "Pass content (Sleuth encrypts and stores) or filename plus https url / object_key. "
            "Return JSON files[]. Do not embed data-URLs."
        ),
    )
    def emit_file_tool(
        filename: str = "output.txt",
        content: str = "",
        url: str = "",
        mime: str = "text/plain",
        object_key: str = "",
        size: int = 0,
    ) -> str:
        return json.dumps(
            emit_file(
                settings,
                filename=filename,
                content=content,
                url=url,
                mime=mime,
                object_key=object_key,
                size=size,
            ),
            ensure_ascii=False,
        )
