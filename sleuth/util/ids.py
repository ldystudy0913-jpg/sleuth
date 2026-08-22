"""Identifier generation.

Persistent IDs (session / message / part / tool_use) MUST be globally unique
across process restarts and multiple workers — never reuse a process-local
monotonic counter for MySQL/SQLite primary keys.
"""
from __future__ import annotations

import secrets


def reset() -> None:
    """No-op kept for test compatibility (IDs are random, not sequenced)."""
    return


def message_id() -> str:
    return "msg_" + secrets.token_hex(12)


def tool_use_id() -> str:
    return "toolu_" + secrets.token_hex(12)


def session_id() -> str:
    return "sess_" + secrets.token_hex(12)


def part_id() -> str:
    return "part_" + secrets.token_hex(12)


def file_id() -> str:
    return "file_" + secrets.token_hex(12)
