"""Identifier generation.

Mirrors opencode's monotonic id schemes (msg_<n>, toolu_<n>, sess_...).
A process-global counter keeps ids ascending within a run so message
ordering is stable when persisted to disk.
"""
from __future__ import annotations

import secrets
from itertools import count

_counter = count(1)


def reset() -> None:
    """Reset the global counter (mainly for tests)."""
    global _counter
    _counter = count(1)


def _next() -> int:
    return next(_counter)


def message_id() -> str:
    return f"msg_{_next()}"


def tool_use_id() -> str:
    # Use a "toolu_" prefix so ids are obvious in logs and round-trip cleanly.
    return "toolu_" + secrets.token_hex(12)


def session_id() -> str:
    return "sess_" + secrets.token_hex(12)


def part_id() -> str:
    return f"part_{_next()}"
