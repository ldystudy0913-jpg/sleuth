"""Minimal .env loader (zero-dependency).

We avoid pulling in python-dotenv to keep the install footprint tiny. This
parser handles the common cases: KEY=VALUE, quoted values, # comments, and
blank lines. It only sets vars that are not already in the environment, so
a real shell export always wins.

Looked up from (in order): the project cwd `.env`, then `~/.config/opencode/.env`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None
    # export prefix is allowed
    if key.startswith("export "):
        key = key[len("export ") :].strip()
    value = value.strip()
    # strip surrounding quotes
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def _load_file(path: Path) -> int:
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        kv = _parse_line(line)
        if kv is None:
            continue
        key, value = kv
        # never overwrite an existing env var — shell/IDE wins
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_dotenv(cwd: Path | None = None) -> int:
    """Load .env files; returns the number of vars set."""
    total = 0
    candidates: List[Path] = []
    if cwd is not None:
        candidates.append(cwd / ".env")
    candidates.append(Path.home() / ".config" / "opencode" / ".env")
    for p in candidates:
        total += _load_file(p)
    return total
