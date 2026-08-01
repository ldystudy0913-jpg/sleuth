"""Write tool — overwrite or create a file with the given content."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult
from .read import _resolve  # reuse the same path resolver


class WriteParams(BaseModel):
    file_path: str = Field(description="The absolute path to the file to write (must be absolute).")
    content: str = Field(description="The content to write to the file.")


class WriteTool:
    name = "write"
    description = (
        "Overwrite a file with new content, creating it if it does not exist. "
        "You MUST read the file first if it already exists. Prefer the edit "
        "tool for changing existing files. Never create documentation or other "
        "files unless explicitly asked."
    )
    params = WriteParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = WriteParams(**args)
        path = _resolve(p.file_path, ctx.workdir)

        ctx.ask("write", patterns=[str(path)], always=[str(path)])

        existed = path.is_file()
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(p.content, encoding="utf-8")
        except Exception as exc:
            return ToolResult.error("write", f"could not write {path}: {exc}")
        return ToolResult.success(
            "write", "Wrote file successfully.", path=str(path), existed=existed
        )
