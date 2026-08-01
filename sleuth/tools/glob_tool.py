"""Glob tool — fast file pattern matching, sorted by mtime."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult

MAX_RESULTS = 100


class GlobParams(BaseModel):
    pattern: str = Field(description="Glob pattern to match files against, e.g. '**/*.py'.")
    path: Optional[str] = Field(default=None, description="Directory to search in. Defaults to cwd.")


class GlobTool:
    name = "glob"
    description = (
        "Fast file pattern matching. Returns matching file paths sorted by "
        "most recent modification time. Patterns use fnmatch-style syntax "
        "with '**' for recursive matching. At most 100 results are returned."
    )
    params = GlobParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = GlobParams(**args)
        base = Path(p.path) if p.path and Path(p.path).is_absolute() else ctx.workdir / (p.path or "")
        if not base.exists():
            return ToolResult.error("glob", f"directory does not exist: {base}")

        matches = sorted(base.glob(p.pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        truncated = len(matches) > MAX_RESULTS
        matches = matches[:MAX_RESULTS]

        if not matches:
            return ToolResult.success("glob", "No files found.", count=0)
        out = "\n".join(str(m) for m in matches)
        return ToolResult.success("glob", out, count=len(matches), truncated=truncated)
