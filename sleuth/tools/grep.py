"""Grep tool — regex content search across files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from ..guardrails import deny_if_protected, filter_unprotected_paths
from .base import Tool, ToolContext, ToolResult

MAX_MATCHES = 100


class GrepParams(BaseModel):
    pattern: str = Field(description="Regex pattern to search for in file contents.")
    path: Optional[str] = Field(default=None, description="Directory to search in. Defaults to cwd.")
    include: Optional[str] = Field(
        default=None, description='File glob filter, e.g. "*.py" or "*.{ts,tsx}".'
    )


class GrepTool:
    name = "grep"
    description = (
        "Search file contents with a regex. Returns matching lines with file "
        "paths and line numbers, grouped by file. At most 100 matches are "
        "returned. Use `include` to restrict by filename glob."
    )
    params = GrepParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = GrepParams(**args)
        base = Path(p.path) if p.path and Path(p.path).is_absolute() else ctx.workdir / (p.path or "")
        if not base.exists():
            return ToolResult.error("grep", f"directory does not exist: {base}")
        denied = deny_if_protected(
            base, workdir=ctx.workdir, enabled=ctx.guardrails_enabled
        )
        if denied:
            return ToolResult.error("grep", denied)

        try:
            regex = re.compile(p.pattern)
        except re.error as exc:
            return ToolResult.error("grep", f"invalid regex: {exc}")

        glob_pat = p.include or "**/*"
        results: List[str] = []
        total = 0
        truncated = False

        candidates = filter_unprotected_paths(
            base.glob(glob_pat),
            workdir=ctx.workdir,
            enabled=ctx.guardrails_enabled,
        )
        for fpath in candidates:
            if not fpath.is_file():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            file_lines: List[str] = []
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    file_lines.append(f"  Line {i}: {line.strip()}")
                    total += 1
                    if total >= MAX_MATCHES:
                        truncated = True
                        break
            if file_lines:
                results.append(f"{fpath}:\n" + "\n".join(file_lines))
            if truncated:
                break

        if not results:
            return ToolResult.success("grep", "No matches found.", matches=0)
        out = f"Found {total} match(es)\n\n" + "\n\n".join(results)
        return ToolResult.success("grep", out, matches=total, truncated=truncated)
