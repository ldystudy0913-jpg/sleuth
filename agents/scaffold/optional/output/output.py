"""Optional generated-file stub. Return files[] for Sleuth harvest (https url or object_key)."""
from __future__ import annotations

import json
from typing import Any, Dict
from urllib.parse import urlparse

from .config import Settings


def emit_file(
    *,
    filename: str,
    url: str = "",
    mime: str = "text/plain",
    object_key: str = "",
    size: int = 0,
) -> Dict[str, Any]:
    """Build the Sleuth files[] contract. TODO: upload to your object store and put https url."""
    name = (filename or "output.txt").strip() or "output.txt"
    href = (url or "").strip()
    key = (object_key or "").strip()
    if href:
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            return {
                "ok": False,
                "detail": "url must be http(s); data: and file: are not allowed",
                "files": [],
            }
    if not href and not key:
        return {
            "ok": False,
            "detail": (
                "provide https url or object_key after you upload; "
                "or call Sleuth save_output_file instead"
            ),
            "files": [],
        }
    entry: Dict[str, Any] = {
        "filename": name,
        "mime": (mime or "text/plain").strip() or "text/plain",
        "size": int(size or 0),
    }
    if href:
        entry["url"] = href
    if key:
        entry["object_key"] = key
    return {"ok": True, "files": [entry]}


def register(server: Any, settings: Settings) -> None:
    del settings

    @server.tool(
        name="emit_file",
        description=(
            "Register a generated file for the Sleuth session mailbox. "
            "Return JSON files[] with filename plus https url or object_key. "
            "Do not embed bytes or data-URLs. Alternatively the model may call "
            "Sleuth save_output_file to write text into the session COS mailbox."
        ),
    )
    def emit_file_tool(
        filename: str = "output.txt",
        url: str = "",
        mime: str = "text/plain",
        object_key: str = "",
        size: int = 0,
    ) -> str:
        return json.dumps(
            emit_file(
                filename=filename,
                url=url,
                mime=mime,
                object_key=object_key,
                size=size,
            ),
            ensure_ascii=False,
        )
