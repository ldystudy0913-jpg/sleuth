"""Minimal markdown frontmatter parser for `.opencode/agent|command/*.md`.

Port of opencode `config/markdown.ts` (without full YAML). Handles the common
`---` / `---` fenced block with `key: value` and one-level nested maps.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple


@dataclass
class MarkdownDoc:
    data: Dict[str, Any]
    content: str


def parse(text: str) -> MarkdownDoc:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return MarkdownDoc(data={}, content=text.strip())

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return MarkdownDoc(data={}, content=text.strip())

    data = _parse_simple_yaml("\n".join(lines[1:end]))
    body = "\n".join(lines[end + 1 :]).strip()
    return MarkdownDoc(data=data, content=body)


def parse_file(path: Path) -> MarkdownDoc:
    return parse(path.read_text(encoding="utf-8", errors="replace"))


def _parse_simple_yaml(src: str) -> Dict[str, Any]:
    """Tiny YAML subset: scalars, booleans, ints, and one-level nested maps."""
    root: Dict[str, Any] = {}
    current_map: Dict[str, Any] | None = None
    current_key: str | None = None

    for raw in src.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent >= 2 and current_key is not None:
            if current_map is None:
                current_map = {}
                root[current_key] = current_map
            if ":" in line:
                k, _, v = line.partition(":")
                current_map[k.strip()] = _scalar(v.strip())
            continue
        current_map = None
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == "":
            current_key = key
            current_map = {}
            root[key] = current_map
        else:
            current_key = None
            root[key] = _scalar(rest)
    return root


def _scalar(value: str) -> Any:
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        return value[1:-1]
    lower = value.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def entry_name_from_path(relative: str, prefixes: Tuple[str, ...]) -> str:
    """Port of opencode `configEntryNameFromPath` — strip prefix + `.md`."""
    rel = relative.replace("\\", "/")
    for prefix in prefixes:
        if rel.startswith(prefix):
            rel = rel[len(prefix) :]
            break
    if rel.endswith(".md"):
        rel = rel[:-3]
    return rel.replace("/", "-")
