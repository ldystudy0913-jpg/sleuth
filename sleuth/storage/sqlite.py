"""SQLite-backed Store (stdlib sqlite3).

Schema mirrors opencode's packages/core/src/session/sql.ts:
  - session:  metadata + token/cost totals
  - message:  id, session_id, data(JSON)  (one row per Message)
  - part:     id, message_id, session_id, data(JSON)  (one row per block;
              polymorphic via the `type` field inside the JSON)
  - todo:     session_id, content, status, priority, position

No encryption at rest — opencode stores plaintext JSON too (see plan's
SKIP list; GM/MySQL encryption is intentionally not implemented).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..messages import Message, block_from_dict, block_to_dict
from ..util.ids import message_id, part_id
from .base import SessionRecord, Store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    directory TEXT NOT NULL,
    title TEXT NOT NULL,
    agent TEXT,
    model TEXT,
    cost REAL NOT NULL DEFAULT 0,
    tokens_input INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    tokens_reasoning INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    permission TEXT,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_directory_idx ON session(directory);

CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS message_session_seq_idx ON message(session_id, seq);

CREATE TABLE IF NOT EXISTS part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    time_created INTEGER NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS part_message_idx ON part(message_id, id);
CREATE INDEX IF NOT EXISTS part_session_idx ON part(session_id);

CREATE TABLE IF NOT EXISTS todo (
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    PRIMARY KEY (session_id, position),
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
"""


def default_db_path() -> Path:
    """~/.local/share/opencode/sleuth.db (OPENCODE_DATA_DIR overrides)."""
    import os

    base = os.environ.get("OPENCODE_DATA_DIR")
    if not base:
        home = Path.home()
        if os.name == "nt":
            base = str(home / "AppData" / "Local" / "opencode")
        else:
            base = str(home / ".local" / "share" / "opencode")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p / "sleuth.db"


class SQLiteStore(Store):
    """A simple connection-per-call SQLite store (good enough for an MVP)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_db_path()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ---- session ----

    def create_session(self, rec: SessionRecord) -> None:
        now = int(time.time() * 1000)
        rec.time_created = rec.time_created or now
        rec.time_updated = rec.time_updated or now
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO session
                   (id, directory, title, agent, model, cost,
                    tokens_input, tokens_output, tokens_reasoning,
                    tokens_cache_read, tokens_cache_write,
                    metadata, permission, time_created, time_updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec.id, rec.directory, rec.title, rec.agent,
                    json.dumps(rec.model) if rec.model else None,
                    rec.cost,
                    rec.tokens_input, rec.tokens_output, rec.tokens_reasoning,
                    rec.tokens_cache_read, rec.tokens_cache_write,
                    json.dumps(rec.metadata, default=str),
                    json.dumps(rec.permission) if rec.permission else None,
                    rec.time_created, rec.time_updated,
                ),
            )

    def update_session(self, rec: SessionRecord) -> None:
        rec.time_updated = int(time.time() * 1000)
        with self._conn() as c:
            c.execute(
                """UPDATE session SET
                   title=?, agent=?, model=?, cost=?,
                   tokens_input=?, tokens_output=?, tokens_reasoning=?,
                   tokens_cache_read=?, tokens_cache_write=?,
                   metadata=?, permission=?, time_updated=?
                   WHERE id=?""",
                (
                    rec.title, rec.agent,
                    json.dumps(rec.model) if rec.model else None,
                    rec.cost,
                    rec.tokens_input, rec.tokens_output, rec.tokens_reasoning,
                    rec.tokens_cache_read, rec.tokens_cache_write,
                    json.dumps(rec.metadata, default=str),
                    json.dumps(rec.permission) if rec.permission else None,
                    rec.time_updated, rec.id,
                ),
            )

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(self, directory: Optional[str] = None, limit: int = 50) -> List[SessionRecord]:
        with self._conn() as c:
            if directory:
                rows = c.execute(
                    "SELECT * FROM session WHERE directory=? ORDER BY time_updated DESC LIMIT ?",
                    (directory, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM session ORDER BY time_updated DESC LIMIT ?", (limit,)
                ).fetchall()
        return [_row_to_session(r) for r in rows]

    # ---- messages ----

    def save_message(self, session_id: str, message: Message) -> str:
        """Persist a message + its content blocks. Idempotent: re-saving
        replaces the message and its parts (keyed by message id)."""
        msg_id = message.metadata.get("id") or message_id()
        message.metadata["id"] = msg_id
        now = int(time.time() * 1000)
        data = {
            "role": message.role,
            "metadata": {k: v for k, v in message.metadata.items() if k != "id"},
        }
        with self._conn() as c:
            seq = _next_seq(c, session_id)
            c.execute(
                """INSERT OR REPLACE INTO message
                   (id, session_id, seq, time_created, time_updated, data)
                   VALUES (?,?,?,?,?,?)""",
                (msg_id, session_id, seq, now, now, json.dumps(data, default=str)),
            )
            # replace parts for this message
            c.execute("DELETE FROM part WHERE message_id=?", (msg_id,))
            for i, block in enumerate(message.content):
                c.execute(
                    """INSERT INTO part
                       (id, message_id, session_id, seq, time_created, data)
                       VALUES (?,?,?,?,?,?)""",
                    (part_id(), msg_id, session_id, i, now,
                     json.dumps(block_to_dict(block), default=str)),
                )
        return msg_id

    def load_messages(self, session_id: str) -> List[Message]:
        with self._conn() as c:
            msg_rows = c.execute(
                "SELECT * FROM message WHERE session_id=? ORDER BY seq ASC", (session_id,)
            ).fetchall()
            if not msg_rows:
                return []
            ids = [r["id"] for r in msg_rows]
            placeholders = ",".join("?" * len(ids))
            part_rows = c.execute(
                f"SELECT * FROM part WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
                ids,
            ).fetchall()

        parts_by_msg: Dict[str, List[dict]] = {}
        for pr in part_rows:
            parts_by_msg.setdefault(pr["message_id"], []).append(json.loads(pr["data"]))

        out: List[Message] = []
        for mr in msg_rows:
            data = json.loads(mr["data"])
            blocks = [block_from_dict(d) for d in parts_by_msg.get(mr["id"], [])]
            meta = dict(data.get("metadata") or {})
            meta["id"] = mr["id"]
            out.append(Message(role=data.get("role", "user"), content=blocks, metadata=meta))
        return out

    # ---- todos ----

    def save_todos(self, session_id: str, todos: List[dict]) -> None:
        now = int(time.time() * 1000)
        with self._conn() as c:
            c.execute("DELETE FROM todo WHERE session_id=?", (session_id,))
            for i, t in enumerate(todos):
                c.execute(
                    "INSERT INTO todo (session_id, position, content, status, priority, time_created) VALUES (?,?,?,?,?,?)",
                    (session_id, i, t.get("content", ""), t.get("status", "pending"),
                     t.get("priority", "normal"), now),
                )

    def load_todos(self, session_id: str) -> List[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM todo WHERE session_id=? ORDER BY position ASC", (session_id,)
            ).fetchall()
        return [{"content": r["content"], "status": r["status"], "priority": r["priority"]} for r in rows]

    # ---- delete ----

    def delete_session(self, session_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM session WHERE id=?", (session_id,))


def _next_seq(c: sqlite3.Connection, session_id: str) -> int:
    row = c.execute("SELECT MAX(seq) AS m FROM message WHERE session_id=?", (session_id,)).fetchone()
    return (row["m"] + 1) if row and row["m"] is not None else 1


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    model = json.loads(row["model"]) if row["model"] else None
    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
    permission = json.loads(row["permission"]) if row["permission"] else None
    return SessionRecord(
        id=row["id"],
        directory=row["directory"],
        title=row["title"],
        agent=row["agent"] or "build",
        model=model,
        cost=row["cost"],
        tokens_input=row["tokens_input"],
        tokens_output=row["tokens_output"],
        tokens_reasoning=row["tokens_reasoning"],
        tokens_cache_read=row["tokens_cache_read"],
        tokens_cache_write=row["tokens_cache_write"],
        metadata=metadata,
        permission=permission,
        time_created=row["time_created"],
        time_updated=row["time_updated"],
    )
