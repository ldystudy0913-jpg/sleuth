"""MySQL-backed Store (PyMySQL)."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from ..messages import Message, block_from_dict, block_to_dict
from ..util.ids import message_id, part_id
from .base import SessionRecord, Store, UsageEvent

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS session (
        id VARCHAR(64) PRIMARY KEY,
        directory TEXT NOT NULL,
        title TEXT NOT NULL,
        agent VARCHAR(64),
        user_id VARCHAR(128) NOT NULL DEFAULT 'local',
        model TEXT,
        cost DOUBLE NOT NULL DEFAULT 0,
        tokens_input BIGINT NOT NULL DEFAULT 0,
        tokens_output BIGINT NOT NULL DEFAULT 0,
        tokens_reasoning BIGINT NOT NULL DEFAULT 0,
        tokens_cache_read BIGINT NOT NULL DEFAULT 0,
        tokens_cache_write BIGINT NOT NULL DEFAULT 0,
        metadata JSON,
        permission JSON,
        time_created BIGINT NOT NULL,
        time_updated BIGINT NOT NULL,
        INDEX session_user_idx (user_id, time_updated)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS message (
        id VARCHAR(64) PRIMARY KEY,
        session_id VARCHAR(64) NOT NULL,
        seq INT NOT NULL,
        time_created BIGINT NOT NULL,
        time_updated BIGINT NOT NULL,
        data JSON NOT NULL,
        INDEX message_session_seq_idx (session_id, seq),
        CONSTRAINT fk_message_session FOREIGN KEY (session_id)
            REFERENCES session(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS part (
        id VARCHAR(64) PRIMARY KEY,
        message_id VARCHAR(64) NOT NULL,
        session_id VARCHAR(64) NOT NULL,
        seq INT NOT NULL,
        time_created BIGINT NOT NULL,
        data JSON NOT NULL,
        INDEX part_message_idx (message_id, id),
        INDEX part_session_idx (session_id),
        CONSTRAINT fk_part_message FOREIGN KEY (message_id)
            REFERENCES message(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS todo (
        session_id VARCHAR(64) NOT NULL,
        position INT NOT NULL,
        content TEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        priority VARCHAR(32) NOT NULL,
        time_created BIGINT NOT NULL,
        PRIMARY KEY (session_id, position),
        CONSTRAINT fk_todo_session FOREIGN KEY (session_id)
            REFERENCES session(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_event (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id VARCHAR(128) NOT NULL,
        session_id VARCHAR(64) NOT NULL,
        message_id VARCHAR(64) NOT NULL,
        model VARCHAR(256),
        tokens_input BIGINT NOT NULL DEFAULT 0,
        tokens_output BIGINT NOT NULL DEFAULT 0,
        tokens_reasoning BIGINT NOT NULL DEFAULT 0,
        tokens_cache_read BIGINT NOT NULL DEFAULT 0,
        tokens_cache_write BIGINT NOT NULL DEFAULT 0,
        cost DOUBLE NOT NULL DEFAULT 0,
        time_created BIGINT NOT NULL,
        INDEX usage_user_idx (user_id, time_created)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


class MySQLStore(Store):
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 3306,
        user: str = "sleuth",
        password: str = "",
        database: str = "sleuth",
    ):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                'MySQL storage requires PyMySQL: pip install "sleuth[mysql]"'
            ) from exc
        self._pymysql = pymysql
        self._kwargs = dict(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        self._init()

    def _conn(self):
        return self._pymysql.connect(**self._kwargs)

    def _init(self) -> None:
        with self._conn() as c:
            with c.cursor() as cur:
                for stmt in _SCHEMA:
                    cur.execute(stmt)

    def create_session(self, rec: SessionRecord) -> None:
        now = int(time.time() * 1000)
        rec.time_created = rec.time_created or now
        rec.time_updated = rec.time_updated or now
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO session
                   (id, directory, title, agent, user_id, model, cost,
                    tokens_input, tokens_output, tokens_reasoning,
                    tokens_cache_read, tokens_cache_write,
                    metadata, permission, time_created, time_updated)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     directory=VALUES(directory), title=VALUES(title),
                     agent=VALUES(agent), user_id=VALUES(user_id),
                     time_updated=VALUES(time_updated)""",
                (
                    rec.id, rec.directory, rec.title, rec.agent, rec.user_id or "local",
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
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                """UPDATE session SET
                   title=%s, agent=%s, user_id=%s, model=%s, cost=%s,
                   tokens_input=%s, tokens_output=%s, tokens_reasoning=%s,
                   tokens_cache_read=%s, tokens_cache_write=%s,
                   metadata=%s, permission=%s, time_updated=%s
                   WHERE id=%s""",
                (
                    rec.title, rec.agent, rec.user_id or "local",
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
        with self._conn() as c, c.cursor() as cur:
            cur.execute("SELECT * FROM session WHERE id=%s", (session_id,))
            row = cur.fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(
        self,
        directory: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[SessionRecord]:
        clauses = []
        args: List[Any] = []
        if directory:
            clauses.append("directory=%s")
            args.append(directory)
        if user_id:
            clauses.append("user_id=%s")
            args.append(user_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(limit)
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                f"SELECT * FROM session{where} ORDER BY time_updated DESC LIMIT %s",
                args,
            )
            rows = cur.fetchall()
        return [_row_to_session(r) for r in rows]

    def save_message(self, session_id: str, message: Message) -> str:
        msg_id = message.metadata.get("id") or message_id()
        message.metadata["id"] = msg_id
        now = int(time.time() * 1000)
        data = {
            "role": message.role,
            "metadata": {k: v for k, v in message.metadata.items() if k != "id"},
        }
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS n FROM message WHERE session_id=%s",
                (session_id,),
            )
            seq = int(cur.fetchone()["n"])
            cur.execute(
                """INSERT INTO message
                   (id, session_id, seq, time_created, time_updated, data)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     time_updated=VALUES(time_updated), data=VALUES(data)""",
                (msg_id, session_id, seq, now, now, json.dumps(data, default=str)),
            )
            cur.execute("DELETE FROM part WHERE message_id=%s", (msg_id,))
            for i, block in enumerate(message.content):
                cur.execute(
                    """INSERT INTO part
                       (id, message_id, session_id, seq, time_created, data)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (part_id(), msg_id, session_id, i, now,
                     json.dumps(block_to_dict(block), default=str)),
                )
        return msg_id

    def load_messages(self, session_id: str) -> List[Message]:
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT * FROM message WHERE session_id=%s ORDER BY seq ASC",
                (session_id,),
            )
            msg_rows = cur.fetchall()
            if not msg_rows:
                return []
            ids = [r["id"] for r in msg_rows]
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"SELECT * FROM part WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
                ids,
            )
            part_rows = cur.fetchall()

        parts_by_msg: Dict[str, List[dict]] = {}
        for pr in part_rows:
            data = pr["data"]
            if isinstance(data, str):
                data = json.loads(data)
            parts_by_msg.setdefault(pr["message_id"], []).append(data)

        out: List[Message] = []
        for mr in msg_rows:
            data = mr["data"]
            if isinstance(data, str):
                data = json.loads(data)
            blocks = [block_from_dict(d) for d in parts_by_msg.get(mr["id"], [])]
            meta = dict(data.get("metadata") or {})
            meta["id"] = mr["id"]
            out.append(Message(role=data.get("role", "user"), content=blocks, metadata=meta))
        return out

    def replace_messages(self, session_id: str, messages: List[Message]) -> None:
        with self._conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM part WHERE session_id=%s", (session_id,))
            cur.execute("DELETE FROM message WHERE session_id=%s", (session_id,))
        for msg in messages:
            self.save_message(session_id, msg)

    def save_todos(self, session_id: str, todos: List[dict]) -> None:
        now = int(time.time() * 1000)
        with self._conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM todo WHERE session_id=%s", (session_id,))
            for i, t in enumerate(todos):
                cur.execute(
                    "INSERT INTO todo (session_id, position, content, status, priority, time_created) VALUES (%s,%s,%s,%s,%s,%s)",
                    (session_id, i, t.get("content", ""), t.get("status", "pending"),
                     t.get("priority", "normal"), now),
                )

    def load_todos(self, session_id: str) -> List[dict]:
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT * FROM todo WHERE session_id=%s ORDER BY position ASC",
                (session_id,),
            )
            rows = cur.fetchall()
        return [{"content": r["content"], "status": r["status"], "priority": r["priority"]} for r in rows]

    def save_usage_event(self, event: UsageEvent) -> None:
        now = event.time_created or int(time.time() * 1000)
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO usage_event
                   (user_id, session_id, message_id, model,
                    tokens_input, tokens_output, tokens_reasoning,
                    tokens_cache_read, tokens_cache_write, cost, time_created)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    event.user_id, event.session_id, event.message_id, event.model,
                    event.tokens_input, event.tokens_output, event.tokens_reasoning,
                    event.tokens_cache_read, event.tokens_cache_write, event.cost, now,
                ),
            )

    def sum_usage(self, user_id: str) -> Dict[str, Any]:
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS n,
                          COALESCE(SUM(tokens_input),0) AS ti,
                          COALESCE(SUM(tokens_output),0) AS to_,
                          COALESCE(SUM(tokens_reasoning),0) AS tr,
                          COALESCE(SUM(cost),0) AS cost
                   FROM usage_event WHERE user_id=%s""",
                (user_id,),
            )
            row = cur.fetchone()
        return {
            "user_id": user_id,
            "events": int(row["n"] or 0),
            "tokens_input": int(row["ti"] or 0),
            "tokens_output": int(row["to_"] or 0),
            "tokens_reasoning": int(row["tr"] or 0),
            "cost": float(row["cost"] or 0),
        }

    def delete_session(self, session_id: str) -> None:
        with self._conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM usage_event WHERE session_id=%s", (session_id,))
            cur.execute("DELETE FROM session WHERE id=%s", (session_id,))


def _row_to_session(row: dict) -> SessionRecord:
    model = row.get("model")
    if isinstance(model, str) and model:
        model = json.loads(model)
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata) if metadata else {}
    permission = row.get("permission")
    if isinstance(permission, str):
        permission = json.loads(permission) if permission else None
    return SessionRecord(
        id=row["id"],
        directory=row["directory"],
        title=row["title"],
        agent=row.get("agent") or "build",
        user_id=row.get("user_id") or "local",
        model=model,
        cost=float(row.get("cost") or 0),
        tokens_input=int(row.get("tokens_input") or 0),
        tokens_output=int(row.get("tokens_output") or 0),
        tokens_reasoning=int(row.get("tokens_reasoning") or 0),
        tokens_cache_read=int(row.get("tokens_cache_read") or 0),
        tokens_cache_write=int(row.get("tokens_cache_write") or 0),
        metadata=metadata or {},
        permission=permission,
        time_created=int(row.get("time_created") or 0),
        time_updated=int(row.get("time_updated") or 0),
    )
