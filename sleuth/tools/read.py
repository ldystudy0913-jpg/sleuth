"""Read tool — read a file (with line numbers) or list a directory.

Also ports opencode's image/PDF attachment path: binary media is returned as
base64 data-URL attachments rather than line-numbered text.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .base import ToolContext, ToolResult

MAX_LINES = 2000
MAX_LINE_CHARS = 2000
MAX_MEDIA_BYTES = 5 * 1024 * 1024

SUPPORTED_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


class ReadParams(BaseModel):
    file_path: str = Field(description="The absolute path to the file or directory to read.")
    offset: Optional[int] = Field(default=None, description="Line number to start reading from (1-indexed).")
    limit: Optional[int] = Field(default=None, description="Maximum number of lines to read (default 2000).")


class ReadTool:
    name = "read"
    description = (
        "Read the contents of a file or list a directory. For files, returns "
        "line-numbered content. For directories, returns the entry listing. "
        "Images (jpeg/png/gif/webp) and PDFs are returned as attachments. "
        "Supports an optional 1-indexed `offset` and a `limit` on the number "
        "of lines returned (default 2000). Paths may be relative to the "
        "working directory."
    )
    params = ReadParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = ReadParams(**args)
        path = _resolve(p.file_path, ctx.workdir)

        if path.is_dir():
            return _list_dir(path)

        if not path.is_file():
            return ToolResult.error("read", f"path does not exist: {path}")

        mime = _sniff_mime(path)
        if mime in SUPPORTED_IMAGE_MIMES or mime == "application/pdf":
            try:
                data = path.read_bytes()
            except Exception as exc:
                return ToolResult.error("read", f"could not read {path}: {exc}")
            if len(data) > MAX_MEDIA_BYTES:
                return ToolResult.error("read", f"file too large for media attach (>{MAX_MEDIA_BYTES} bytes)")
            b64 = base64.b64encode(data).decode("ascii")
            msg = "PDF read successfully" if mime == "application/pdf" else "Image read successfully"
            return ToolResult.success(
                "read",
                msg,
                path=str(path),
                attachments=[{
                    "type": "file",
                    "mime": mime,
                    "url": f"data:{mime};base64,{b64}",
                }],
            )

        # Reject other obvious binaries
        if _looks_binary(path):
            return ToolResult.error("read", f"Cannot read binary file: {path}")

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult.error("read", f"could not read {path}: {exc}")

        lines = text.splitlines()
        offset = max(1, p.offset or 1)
        limit = p.limit or MAX_LINES
        start = offset - 1
        end = min(len(lines), start + limit)
        chunk = lines[start:end]

        rendered = []
        for i, line in enumerate(chunk, start=start + 1):
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + "... [truncated line]"
            rendered.append(f"{i:>6}\t{line}")
        body = "\n".join(rendered)

        truncated = end < len(lines)
        out = (
            f"<path>{path}</path>\n"
            f"<type>file</type>\n"
            f"<content>\n{body}\n</content>"
        )
        if truncated:
            out += f"\n<note>showing lines {start+1}-{end} of {len(lines)}</note>"
        return ToolResult.success(
            "read", out, lines=len(lines), path=str(path), truncated=truncated
        )


def _list_dir(path: Path) -> ToolResult:
    entries = sorted(path.iterdir(), key=lambda e: e.name)
    lines = []
    for e in entries:
        kind = "dir" if e.is_dir() else "file"
        lines.append(f"{e.name}\t{kind}")
    body = "\n".join(lines)
    out = (
        f"<path>{path}</path>\n"
        f"<type>directory</type>\n"
        f"<entries>\n{body}\n</entries>"
    )
    return ToolResult.success("read", out, path=str(path), count=len(entries))


def _resolve(p: str, workdir: Path) -> Path:
    candidate = Path(p)
    if not candidate.is_absolute():
        candidate = workdir / candidate
    return candidate.expanduser().resolve()


def _sniff_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    # magic-byte sniff for common images
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return "application/octet-stream"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:5] == b"%PDF-":
        return "application/pdf"
    return "application/octet-stream"


def _looks_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return False
    if b"\x00" in sample:
        return True
    return False
