"""Read a cached (or just-extracted) excerpt of a session attachment."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ..files.ingest import extract_item, schedule_extract, wait_extracts, write_excerpt_fields
from ..files.mailbox import get_file, session_files, write_session_files
from .base import ToolContext, ToolResult


class ReadSessionFileParams(BaseModel):
    file_id: str = Field(description="Session file id (file_...).")


class ReadSessionFileTool:
    name = "read_session_file"
    description = (
        "Read the extracted text excerpt of a session attachment. "
        "Use when the system excerpt is truncated or still pending. "
        "Does not return ciphertext or raw bytes."
    )
    params = ReadSessionFileParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        session = ctx.session
        if session is None:
            return ToolResult.error(self.name, "session is unavailable")
        file_id = str(args.get("file_id") or "").strip()
        if not file_id:
            return ToolResult.error(self.name, "file_id is required")
        files = session_files(session)
        item = get_file(files, file_id)
        if item is None:
            return ToolResult.error(self.name, "file not found")
        if str(item.get("status") or "") != "ready":
            return ToolResult.error(self.name, "file is not ready")
        if str(item.get("excerpt_status") or "") not in ("ok", "skipped"):
            config = getattr(session, "config", None)
            store = getattr(session, "store", None)
            sid = str(getattr(session, "id", "") or "")
            object_store = getattr(session, "_object_store", None)
            if store is not None and sid and config is not None:
                schedule_extract(
                    config=config,
                    store=store,
                    session_id=sid,
                    file_id=file_id,
                    object_store=object_store,
                )
                wait_extracts(timeout=float(getattr(config.files, "extract_timeout_s", 45) or 45))
                files = session_files(session)
                item = get_file(files, file_id) or item
            elif config is not None:
                excerpt = extract_item(config=config, item=item, object_store=object_store)
                write_excerpt_fields(item, excerpt)
                write_session_files(session, files)
        excerpt = item.get("excerpt") if isinstance(item.get("excerpt"), dict) else {}
        payload = {
            "file_id": file_id,
            "filename": item.get("filename"),
            "mime": item.get("mime"),
            "excerpt_status": item.get("excerpt_status") or "",
            "text": str((excerpt or {}).get("text") or ""),
            "truncated": bool((excerpt or {}).get("truncated")),
            "parser": str((excerpt or {}).get("parser") or ""),
            "skipped": str((excerpt or {}).get("skipped") or ""),
        }
        return ToolResult.success(self.name, json.dumps(payload, ensure_ascii=False))
