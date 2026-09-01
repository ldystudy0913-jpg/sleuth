"""Read a cached (or just-extracted) excerpt of a session attachment."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ..config import FilesConfig
from ..files.extract import resolve_vision_prompt
from ..files.ingest import extract_item, schedule_extract, wait_extracts, write_excerpt_fields
from ..files.mailbox import get_file, session_files, write_session_files
from ..files import settings as file_settings
from .base import ToolContext, ToolResult


class ReadSessionFileParams(BaseModel):
    file_id: str = Field(description="Session file id (file_...).")
    question: str = Field(
        default="",
        description=(
            "Optional user question to re-parse the file. For images and scanned PDFs, "
            "runs vision again focused on this question. For documents, re-extracts with "
            "a higher character limit. Does not replace the stored session excerpt. "
            "Omit to return the cached excerpt."
        ),
    )


class ReadSessionFileTool:
    name = "read_session_file"
    description = (
        "Read the extracted text excerpt of a session attachment. "
        "Use when the system excerpt is truncated, skipped, still pending, or missing "
        "what the user asked. Pass `question` set to the user's original question to "
        "re-parse images/scanned PDFs with vision or re-extract a longer document. "
        "Do not tell the user you cannot see the file. Does not return ciphertext or raw bytes."
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
        question = str(args.get("question") or "").strip()
        files = session_files(session)
        item = get_file(files, file_id)
        if item is None:
            return ToolResult.error(self.name, "file not found")
        cfg = getattr(session, "config", None)
        if str(item.get("status") or "") != file_settings.status_ready(cfg):
            return ToolResult.error(self.name, "file is not ready")
        object_store = getattr(session, "_object_store", None)
        if str(item.get("excerpt_status") or "") not in file_settings.excerpt_done(cfg):
            config = cfg
            store = getattr(session, "store", None)
            sid = str(getattr(session, "id", "") or "")
            if store is not None and sid and config is not None:
                schedule_extract(
                    config=config,
                    store=store,
                    session_id=sid,
                    file_id=file_id,
                    object_store=object_store,
                )
                wait_s = float(
                    getattr(config.files, "extract_timeout_s", 0)
                    or file_settings.files_cfg(config).extract_timeout_s
                )
                wait_extracts(timeout=wait_s)
                files = session_files(session)
                item = get_file(files, file_id) or item
            elif config is not None:
                excerpt = extract_item(config=config, item=item, object_store=object_store)
                write_excerpt_fields(item, excerpt, config)
                write_session_files(session, files)
        files = session_files(session)
        item = get_file(files, file_id) or item
        excerpt = item.get("excerpt") if isinstance(item.get("excerpt"), dict) else {}
        if question and cfg is not None:
            fcfg = file_settings.files_cfg(cfg)
            reread = int(fcfg.excerpt_reread_max_chars or 0) or int(
                FilesConfig().excerpt_reread_max_chars
            )
            focused = extract_item(
                config=cfg,
                item=item,
                object_store=object_store,
                max_chars=reread,
                vision_prompt=resolve_vision_prompt(cfg, question),
            )
            payload = {
                "file_id": file_id,
                "filename": item.get("filename"),
                "mime": item.get("mime"),
                "excerpt_status": item.get("excerpt_status") or "",
                "focused": True,
                "question": question,
                "text": str(focused.text or ""),
                "truncated": bool(focused.truncated),
                "parser": str(focused.parser or ""),
                "skipped": str(focused.skipped or ""),
            }
            return ToolResult.success(self.name, json.dumps(payload, ensure_ascii=False))
        payload = {
            "file_id": file_id,
            "filename": item.get("filename"),
            "mime": item.get("mime"),
            "excerpt_status": item.get("excerpt_status") or "",
            "focused": False,
            "text": str((excerpt or {}).get("text") or ""),
            "truncated": bool((excerpt or {}).get("truncated")),
            "parser": str((excerpt or {}).get("parser") or ""),
            "skipped": str((excerpt or {}).get("skipped") or ""),
        }
        return ToolResult.success(self.name, json.dumps(payload, ensure_ascii=False))
