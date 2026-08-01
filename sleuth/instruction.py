"""Instruction file discovery — port of opencode `session/instruction.ts`.

Discovers AGENTS.md / CLAUDE.md / CONTEXT.md (first match wins per class) from
the global config dir and by walking up from the workdir to the git root.
Also resolves config `instructions` entries that are file paths or http(s) URLs.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Sequence

from .config import Config, _git_root, _global_config_dir


_PROJECT_NAMES = ("AGENTS.md", "CLAUDE.md", "CONTEXT.md")  # CONTEXT.md deprecated


def _global_candidates() -> List[Path]:
    gdir = _global_config_dir()
    home = Path.home()
    return [
        gdir / "AGENTS.md",
        home / ".claude" / "CLAUDE.md",
    ]


def find_up(name: str, start: Path, stop: Optional[Path] = None) -> List[Path]:
    """Walk up from start collecting `name` until stop (inclusive) or fs root."""
    found: List[Path] = []
    cur = start.resolve()
    root = (stop or _git_root(cur) or cur.anchor)
    root_path = Path(root).resolve() if not isinstance(root, Path) else root.resolve()
    while True:
        candidate = cur / name
        if candidate.is_file():
            found.append(candidate)
        if cur == root_path or cur == cur.parent:
            break
        cur = cur.parent
    return found


def discover_paths(workdir: Path, config: Optional[Config] = None) -> List[Path]:
    """Return instruction file paths (global first-match + project first-name)."""
    paths: List[Path] = []
    seen = set()

    for p in _global_candidates():
        if p.is_file():
            rp = p.resolve()
            if rp not in seen:
                paths.append(rp)
                seen.add(rp)
            break

    # First project-level filename that matches wins (don't stack AGENTS+CLAUDE)
    stop = _git_root(workdir)
    for name in _PROJECT_NAMES:
        matches = find_up(name, workdir, stop)
        if matches:
            for m in matches:
                rp = m.resolve()
                if rp not in seen:
                    paths.append(rp)
                    seen.add(rp)
            break

    if config and config.instructions:
        for raw in config.instructions:
            if raw.startswith("http://") or raw.startswith("https://"):
                continue
            expanded = Path(os.path.expanduser(raw))
            if expanded.is_file():
                rp = expanded.resolve()
                if rp not in seen:
                    paths.append(rp)
                    seen.add(rp)

    return paths


def _fetch_url(url: str, timeout: float = 5.0) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sleuth"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""


def load_instruction_texts(workdir: Path, config: Config) -> List[str]:
    """Load discovered files + remote config.instructions URLs as text blocks."""
    blocks: List[str] = []
    for path in discover_paths(workdir, config):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            blocks.append(f"# Instructions from {path.name}\n\n{text}")

    # Inline strings in config.instructions that aren't paths/URLs stay as-is
    # (handled by prompts.assemble). Here we only fetch http(s) entries.
    for raw in config.instructions or []:
        if raw.startswith("http://") or raw.startswith("https://"):
            body = _fetch_url(raw).strip()
            if body:
                blocks.append(f"# Instructions from {raw}\n\n{body}")

    return blocks


def inline_instruction_lines(config: Config, file_paths: Sequence[Path]) -> List[str]:
    """Config instruction strings that are not files/URLs (literal prompt lines)."""
    files = {p.resolve() for p in file_paths}
    out: List[str] = []
    for raw in config.instructions or []:
        if raw.startswith("http://") or raw.startswith("https://"):
            continue
        expanded = Path(os.path.expanduser(raw))
        try:
            if expanded.is_file() and expanded.resolve() in files:
                continue
            if expanded.is_file():
                continue
        except OSError:
            pass
        out.append(raw)
    return out
