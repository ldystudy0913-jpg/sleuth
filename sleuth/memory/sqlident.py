"""Validate SQL identifiers so configured table names cannot inject."""
from __future__ import annotations

import re

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_ident(name: str) -> str:
    raw = (name or "").strip()
    if not _IDENT.fullmatch(raw):
        raise ValueError("invalid SQL identifier")
    return raw
