"""Write a generated text file into the session COS mailbox and return a download ref."""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

from ..files.cos import CosError, CosNotConfigured
from ..files.mailbox import MailboxError, put_generated_text
from .base import ToolContext, ToolResult


class SaveOutputFileParams(BaseModel):
    filename: str = Field(description="Download filename, e.g. reply.md or notes.txt")
    content: str = Field(description="UTF-8 text to store. Do not pass binary or data-URLs.")
    mime: Optional[str] = Field(
        default=None,
        description="Optional MIME type (default text/plain; charset=utf-8).",
    )


class SaveOutputFileTool:
    name = "save_output_file"
    description = (
        "Save generated text as a session file in object storage and return a "
        "download reference. Use this on the default agent when the user should "
        "receive a file (markdown, csv, json). Do not embed file bytes in the reply."
    )
    params = SaveOutputFileParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        session = ctx.session
        if session is None:
            return ToolResult.error(self.name, "session is unavailable")
        try:
            item = put_generated_text(
                session=session,
                filename=str(args.get("filename") or ""),
                content=str(args.get("content") or ""),
                mime=str(args.get("mime") or "") or "",
            )
        except CosNotConfigured as exc:
            return ToolResult.error(self.name, str(exc))
        except CosError as exc:
            return ToolResult.error(self.name, str(exc))
        except MailboxError as exc:
            return ToolResult.error(self.name, str(exc))
        payload = {
            "file_id": item.get("id"),
            "filename": item.get("filename"),
            "mime": item.get("mime"),
            "size": item.get("size"),
            "download_url": f"/v1/sessions/{session.id}/files/{item.get('id')}",
        }
        return ToolResult.success(
            self.name,
            json.dumps(payload, ensure_ascii=False),
            attachments=[
                {
                    "type": "file",
                    "mime": item.get("mime") or "text/plain",
                    "filename": item.get("filename") or "output.txt",
                    "url": payload["download_url"],
                }
            ],
        )
