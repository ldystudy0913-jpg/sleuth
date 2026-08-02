"""Edit tool — exact string replacement with fuzzy fallbacks.

opencode's edit tool runs a *cascade* of "replacers" against the file
content: an exact match first, then progressively fuzzier matchers that
forgive trailing whitespace, indentation drift, and escape normalization.
We port a compact subset of that cascade — enough to be robust against the
small ways a model mangles the old_string it just read:

  1. exact              literal substring match
  2. line-trimmed       match where each search line is trimmed of
                        leading/trailing whitespace against content lines
  3. whitespace-norm    collapse all runs of whitespace to single spaces
  4. indentation-flex   strip common leading indentation from both sides

Rules (matching opencode):
  - old_string == new_string is rejected.
  - For a non-empty file, old_string must not be empty (use write).
  - Without replace_all, the matcher must find exactly one occurrence;
    multiple matches -> error, asking the model to be more specific.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..guardrails import deny_if_protected
from .base import Tool, ToolContext, ToolResult
from .read import _resolve


class EditParams(BaseModel):
    file_path: str = Field(description="The absolute path to the file to modify.")
    old_string: str = Field(description="The exact text to replace.")
    new_string: str = Field(description="The text to replace it with.")
    replace_all: Optional[bool] = Field(
        default=False, description="Replace all occurrences of old_string."
    )


class EditTool:
    name = "edit"
    description = (
        "Perform an exact string replacement in a file. old_string must be "
        "unique within the file unless replace_all is true. Provide enough "
        "surrounding context to make the match unambiguous. The file must be "
        "read before editing. Prefer this over write for changing existing "
        "files."
    )
    params = EditParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = EditParams(**args)
        path = _resolve(p.file_path, ctx.workdir)
        denied = deny_if_protected(
            path, workdir=ctx.workdir, enabled=ctx.guardrails_enabled
        )
        if denied:
            return ToolResult.error("edit", denied)

        if not path.is_file():
            return ToolResult.error("edit", f"file does not exist: {path}")
        if p.old_string == p.new_string:
            return ToolResult.error("edit", "old_string and new_string are identical")
        if not p.old_string:
            return ToolResult.error("edit", "old_string must not be empty")

        ctx.ask("edit", patterns=[str(path)], always=[str(path)])

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult.error("edit", f"could not read {path}: {exc}")

        try:
            new_content = replace(content, p.old_string, p.new_string, p.replace_all or False)
        except ReplaceError as exc:
            return ToolResult.error("edit", str(exc))

        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return ToolResult.error("edit", f"could not write {path}: {exc}")

        return ToolResult.success(
            "edit", "Edited file successfully.", path=str(path)
        )


class ReplaceError(Exception):
    """Raised when no unique match can be established."""


def replace(content: str, old: str, new: str, replace_all: bool = False) -> str:
    """Run the replacer cascade. Raises ReplaceError on no/ambiguous match."""
    replacers = [
        _exact,
        _line_trimmed,
        _whitespace_normalized,
        _indentation_flexible,
    ]
    last_error = "Could not find old_string in the file."
    for replacer in replacers:
        try:
            return replacer(content, old, new, replace_all)
        except ReplaceError as exc:
            last_error = str(exc)
            continue
    raise ReplaceError(last_error)


def _exact(content: str, old: str, new: str, replace_all: bool) -> str:
    count = content.count(old)
    if count == 0:
        raise ReplaceError("no exact match")
    if count > 1 and not replace_all:
        raise ReplaceError(f"found {count} exact matches; add context or use replace_all")
    if replace_all:
        return content.replace(old, new)
    # count == 1
    idx = content.find(old)
    return content[:idx] + new + content[idx + len(old) :]


def _line_trimmed(content: str, old: str, new: str, replace_all: bool) -> str:
    """Match each line by its trimmed form (leading/trailing whitespace ignored)."""
    old_lines = old.splitlines()
    content_lines = content.splitlines(keepends=True)
    if not old_lines:
        raise ReplaceError("empty old_string")
    trimmed_old = [ln.strip() for ln in old_lines]

    matches: list[int] = []  # start line index
    n = len(content_lines)
    m = len(trimmed_old)
    for i in range(n - m + 1):
        window = [content_lines[i + j].strip() for j in range(m)]
        if window == trimmed_old:
            matches.append(i)

    if not matches:
        raise ReplaceError("no line-trimmed match")
    if len(matches) > 1 and not replace_all:
        raise ReplaceError(f"{len(matches)} line-trimmed matches")

    target = matches if replace_all else matches[:1]
    # Replace from the end so earlier indices stay valid. Preserve the
    # trailing newline of the original last matched line on the new block.
    out_lines = content_lines[:]
    for start in sorted(target, reverse=True):
        tail_nl = "\n" if out_lines[start + m - 1].endswith("\n") else ""
        block = new if new.endswith("\n") or not tail_nl else new + tail_nl
        out_lines[start : start + m] = [block]
    return "".join(out_lines)


def _whitespace_normalized(content: str, old: str, new: str, replace_all: bool) -> str:
    """Collapse all whitespace runs to single spaces before comparing."""
    ws = lambda s: re.sub(r"\s+", " ", s)
    norm_content = ws(content)
    norm_old = ws(old)
    norm_new = ws(new)
    if norm_old not in norm_content:
        raise ReplaceError("no whitespace-normalized match")
    count = norm_content.count(norm_old)
    if count > 1 and not replace_all:
        raise ReplaceError(f"{count} whitespace-normalized matches")
    # We can only safely do replace_all semantics here because positions shift
    # in the normalised space. For a single match, map back to original.
    if replace_all:
        # Approximate: operate on the whitespace-normalised content. This loses
        # original formatting, so only accept it when replace_all is requested.
        return norm_content.replace(norm_old, norm_new)
    # single match: find span in original content by scanning
    return _replace_first_ws(content, old, new)


def _replace_first_ws(content: str, old: str, new: str) -> str:
    """Find the first whitespace-insensitive match and replace it."""
    ws = lambda s: re.sub(r"\s+", " ", s)
    norm_old = ws(old)
    # build a regex from norm_old, escaping, allowing flexible whitespace
    pattern = re.escape(norm_old).replace(r"\ ", r"\s+")
    m = re.search(pattern, content)
    if not m:
        raise ReplaceError("no whitespace-normalized match")
    return content[: m.start()] + new + content[m.end() :]


def _indentation_flexible(content: str, old: str, new: str, replace_all: bool) -> str:
    """Strip common leading whitespace from old and content blocks, then match."""
    old_dedent = _dedent(old)
    content_dedent = _dedent(content)
    if old_dedent not in content_dedent:
        raise ReplaceError("no indentation-flexible match")
    # map the match back onto the original content by searching the dedented
    # positions is non-trivial; approximate by doing an exact match against
    # the dedented content (preserves the file's own dedented form).
    count = content_dedent.count(old_dedent)
    if count > 1 and not replace_all:
        raise ReplaceError(f"{count} indentation-flexible matches")
    if count == 1:
        return content_dedent.replace(old_dedent, new, 1)
    raise ReplaceError("no indentation-flexible match")


def _dedent(text: str) -> str:
    """Remove common leading whitespace from all lines."""
    lines = text.expandtabs().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return text
    prefix = min((len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()), default=0)
    return "\n".join(ln[prefix:] for ln in lines)
