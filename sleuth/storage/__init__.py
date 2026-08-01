"""Persistence layer.

opencode persists sessions/messages/parts to SQLite via Drizzle. We port the
same shape with the stdlib `sqlite3` driver (no new dependency) — see
sqlite.py for the schema and base.py for the Store protocol.
"""
from .base import Store, SessionRecord
from .sqlite import SQLiteStore

__all__ = ["Store", "SessionRecord", "SQLiteStore"]
