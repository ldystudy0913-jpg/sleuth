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
CREATE TABLE IF NOT EXISTS `session_metadata`( 
    `id` VARCHAR(64) NOT NULL,
    `work_directory` TEXT NOT NULL,
    `title` TEXT NOT NULL,
    `agent` VARCHAR(64),
    `user_id` VARCHAR(128) NOT NULL DEFAULT 'local',
    `model` TEXT,
    `cost` DECIMAL(10,2) NOT NULL DEFAULT 0,
    `tokens_input` BIGINT NOT NULL DEFAULT 0,
    `tokens_output` BIGINT NOT NULL DEFAULT 0,
    `tokens_reasoning` BIGINT NOT NULL DEFAULT 0,
    `tokens_cache_read` BIGINT NOT NULL DEFAULT 0,
    `tokens_cache_write` BIGINT NOT NULL DEFAULT 0,
    `metadata` TEXT,
    `permission` TEXT,
    `time_created` BIGINT NOT NULL,
    `time_updated` BIGINT NOT NULL,
    INDEX `session_user_idx`(`user_id`,`time_updated`),
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='会话元数据';
    """,
    """
CREATE TABLE IF NOT EXISTS `message`( 
    `id` VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    `session_id` VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `seq` INT NOT NULL,
    `time_created` BIGINT NOT NULL,
    `time_updated` BIGINT NOT NULL,
    `message_data` TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    INDEX `message_session_seq_idx`(`session_id`,`seq`),
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='消息元数据';
    """,
    """
CREATE TABLE IF NOT EXISTS `part`( 
    `id` VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    `message_id` VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `session_id` VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `seq` INT NOT NULL,
    `time_created` BIGINT NOT NULL,
    `data_block` TEXT NOT NULL,
    INDEX `part_message_idx`(`message_id`,`id`),
    INDEX `part_session_idx`(`session_id`),
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='消息内容块';
    """,
    """
CREATE TABLE IF NOT EXISTS `todo`( 
    `session_id` VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `position` INT NOT NULL,
    `content` TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `item_status` VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `priority` VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    `time_created` BIGINT NOT NULL,
    PRIMARY KEY (`session_id`, `position`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='待办事项';
    """,
    """
CREATE TABLE IF NOT EXISTS `usage_event`( 
    `id` BIGINT AUTO_INCREMENT NOT NULL,
    `user_id` VARCHAR(128) NOT NULL,
    `session_id` VARCHAR(64) NOT NULL,
    `message_id` VARCHAR(64) NOT NULL,
    `model` VARCHAR(256),
    `tokens_input` BIGINT NOT NULL DEFAULT 0,
    `tokens_output` BIGINT NOT NULL DEFAULT 0,
    `tokens_reasoning` BIGINT NOT NULL DEFAULT 0,
    `tokens_cache_read` BIGINT NOT NULL DEFAULT 0,
    `tokens_cache_write` BIGINT NOT NULL DEFAULT 0,
    `cost` DECIMAL(10,2) NOT NULL DEFAULT 0,
    `time_created` BIGINT NOT NULL,
    INDEX `usage_user_idx`(`user_id`,`time_created`),
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='使用量统计';
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
        use_log_trace: bool = False,
    ):
        self._use_log_trace = bool(use_log_trace)
        if self._use_log_trace:
            from log_trace.mysql_log_trace import DictCursor, MySql

            self._MySql = MySql
            self._dict_cursor = DictCursor
            self._pymysql = None
            from ..logtrace import trace_span

            with trace_span(host="sleuth", api="JOB:/mysql_init"):
                self._init()
            return
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

    def _ph(self) -> str:
        """log_trace Cursor.parse_sql treats % as modulo; use ? then library maps to %s."""
        return "?" if self._use_log_trace else "%s"

    def _ps(self, n: int) -> str:
        return ",".join(self._ph() for _ in range(n))

    def _log_trace_conn(self):
        from ..logtrace import config_file

        return self._MySql(
            config_path=config_file(),
            autocommit=True,
            cursorclass=self._dict_cursor,
        )

    def _conn(self):
        if self._use_log_trace:
            return self._log_trace_conn()
        return self._pymysql.connect(**self._kwargs)

    def _init(self) -> None:
        with self._conn() as c, c.cursor() as cur:
            for stmt in _SCHEMA:
                cur.execute(stmt)

    def _session_insert_args(self, rec: SessionRecord) -> tuple:
        return (
            rec.id,
            rec.directory,
            rec.title,
            rec.agent,
            rec.user_id or "local",
            json.dumps(rec.model) if rec.model else None,
            rec.cost,
            rec.tokens_input,
            rec.tokens_output,
            rec.tokens_reasoning,
            rec.tokens_cache_read,
            rec.tokens_cache_write,
            json.dumps(rec.metadata, default=str),
            json.dumps(rec.permission) if rec.permission else None,
            rec.time_created,
            rec.time_updated,
        )

    def create_session(self, rec: SessionRecord) -> None:
        now = int(time.time() * 1000)
        rec.time_created = rec.time_created or now
        rec.time_updated = rec.time_updated or now
        ps = self._ps(16)
        sql = (
            "INSERT INTO session_metadata "
            "(id, work_directory, title, agent, user_id, model, cost, "
            "tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, "
            "metadata, permission, time_created, time_updated) "
            f"VALUES ({ps}) ON DUPLICATE KEY UPDATE "
            "work_directory=VALUES(work_directory), title=VALUES(title), "
            "agent=VALUES(agent), user_id=VALUES(user_id), "
            "time_updated=VALUES(time_updated)"
        )
        with self._conn() as c, c.cursor() as cur:
            cur.execute(sql, self._session_insert_args(rec))

    def update_session(self, rec: SessionRecord) -> None:
        rec.time_updated = int(time.time() * 1000)
        p = self._ph()
        sql = (
            "UPDATE session_metadata SET "
            f"title={p}, agent={p}, user_id={p}, model={p}, cost={p}, "
            f"tokens_input={p}, tokens_output={p}, tokens_reasoning={p}, "
            f"tokens_cache_read={p}, tokens_cache_write={p}, "
            f"metadata={p}, permission={p}, time_updated={p} "
            f"WHERE id={p}"
        )
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                sql,
                (
                    rec.title,
                    rec.agent,
                    rec.user_id or "local",
                    json.dumps(rec.model) if rec.model else None,
                    rec.cost,
                    rec.tokens_input,
                    rec.tokens_output,
                    rec.tokens_reasoning,
                    rec.tokens_cache_read,
                    rec.tokens_cache_write,
                    json.dumps(rec.metadata, default=str),
                    json.dumps(rec.permission) if rec.permission else None,
                    rec.time_updated,
                    rec.id,
                ),
            )

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        p = self._ph()
        with self._conn() as c, c.cursor() as cur:
            cur.execute(f"SELECT * FROM session_metadata WHERE id={p}", (session_id,))
            row = cur.fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(
        self,
        directory: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[SessionRecord]:
        p = self._ph()
        clauses = []
        args: List[Any] = []
        if directory:
            clauses.append(f"work_directory={p}")
            args.append(directory)
        if user_id:
            clauses.append(f"user_id={p}")
            args.append(user_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(limit)
        sql = f"SELECT * FROM session_metadata{where} ORDER BY time_updated DESC LIMIT {p}"
        with self._conn() as c, c.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        return [_row_to_session(r) for r in rows]

    def _next_message_seq(self, cur, session_id: str) -> int:
        p = self._ph()
        cur.execute(
            f"SELECT COALESCE(MAX(seq),0)+1 AS n FROM message WHERE session_id={p}",
            (session_id,),
        )
        return int(cur.fetchone()["n"])

    def _upsert_message_row(self, cur, msg_id: str, session_id: str, seq: int, now: int, payload: str) -> None:
        ps = self._ps(6)
        sql = (
            "INSERT INTO message "
            "(id, session_id, seq, time_created, time_updated, message_data) "
            f"VALUES ({ps}) ON DUPLICATE KEY UPDATE "
            "time_updated=VALUES(time_updated), message_data=VALUES(message_data)"
        )
        cur.execute(sql, (msg_id, session_id, seq, now, now, payload))

    def _replace_parts(self, cur, msg_id: str, session_id: str, now: int, message: Message) -> None:
        p = self._ph()
        ps = self._ps(6)
        cur.execute(f"DELETE FROM part WHERE message_id={p}", (msg_id,))
        insert_sql = (
            "INSERT INTO part (id, message_id, session_id, seq, time_created, data_block) "
            f"VALUES ({ps})"
        )
        for i, block in enumerate(message.content):
            cur.execute(
                insert_sql,
                (part_id(), msg_id, session_id, i, now, json.dumps(block_to_dict(block), default=str)),
            )

    def save_message(self, session_id: str, message: Message) -> str:
        msg_id = message.metadata.get("id") or message_id()
        message.metadata["id"] = msg_id
        now = int(time.time() * 1000)
        data = {
            "role": message.role,
            "metadata": {k: v for k, v in message.metadata.items() if k != "id"},
        }
        payload = json.dumps(data, default=str)
        with self._conn() as c, c.cursor() as cur:
            seq = self._next_message_seq(cur, session_id)
            self._upsert_message_row(cur, msg_id, session_id, seq, now, payload)
            self._replace_parts(cur, msg_id, session_id, now, message)
        return msg_id

    def _parts_for_messages(self, cur, msg_rows: List[dict]) -> Dict[str, List[dict]]:
        ids = [r["id"] for r in msg_rows]
        placeholders = self._ps(len(ids))
        cur.execute(
            f"SELECT * FROM part WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
            ids,
        )
        parts_by_msg: Dict[str, List[dict]] = {}
        for pr in cur.fetchall():
            parts_by_msg.setdefault(pr["message_id"], []).append(_json_value(pr["data_block"], {}))
        return parts_by_msg

    def load_messages(self, session_id: str) -> List[Message]:
        p = self._ph()
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                f"SELECT * FROM message WHERE session_id={p} ORDER BY seq ASC",
                (session_id,),
            )
            msg_rows = cur.fetchall()
            if not msg_rows:
                return []
            parts_by_msg = self._parts_for_messages(cur, msg_rows)
        return [_message_from_rows(mr, parts_by_msg) for mr in msg_rows]

    def replace_messages(self, session_id: str, messages: List[Message]) -> None:
        p = self._ph()
        with self._conn() as c, c.cursor() as cur:
            cur.execute(f"DELETE FROM part WHERE session_id={p}", (session_id,))
            cur.execute(f"DELETE FROM message WHERE session_id={p}", (session_id,))
        for msg in messages:
            self.save_message(session_id, msg)

    def save_todos(self, session_id: str, todos: List[dict]) -> None:
        now = int(time.time() * 1000)
        p = self._ph()
        ps = self._ps(6)
        insert_sql = (
            "INSERT INTO todo (session_id, position, content, item_status, priority, time_created) "
            f"VALUES ({ps})"
        )
        with self._conn() as c, c.cursor() as cur:
            cur.execute(f"DELETE FROM todo WHERE session_id={p}", (session_id,))
            for i, t in enumerate(todos):
                cur.execute(
                    insert_sql,
                    (
                        session_id,
                        i,
                        t.get("content", ""),
                        t.get("status", "pending"),
                        t.get("priority", "normal"),
                        now,
                    ),
                )

    def load_todos(self, session_id: str) -> List[dict]:
        p = self._ph()
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                f"SELECT * FROM todo WHERE session_id={p} ORDER BY position ASC",
                (session_id,),
            )
            rows = cur.fetchall()
        return [
            {"content": r["content"], "status": r["item_status"], "priority": r["priority"]}
            for r in rows
        ]

    def save_usage_event(self, event: UsageEvent) -> None:
        now = event.time_created or int(time.time() * 1000)
        ps = self._ps(11)
        sql = (
            "INSERT INTO usage_event "
            "(user_id, session_id, message_id, model, "
            "tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, cost, time_created) "
            f"VALUES ({ps})"
        )
        with self._conn() as c, c.cursor() as cur:
            cur.execute(
                sql,
                (
                    event.user_id,
                    event.session_id,
                    event.message_id,
                    event.model,
                    event.tokens_input,
                    event.tokens_output,
                    event.tokens_reasoning,
                    event.tokens_cache_read,
                    event.tokens_cache_write,
                    event.cost,
                    now,
                ),
            )

    def sum_usage(self, user_id: str) -> Dict[str, Any]:
        p = self._ph()
        sql = (
            "SELECT COUNT(*) AS n, "
            "COALESCE(SUM(tokens_input),0) AS ti, "
            "COALESCE(SUM(tokens_output),0) AS to_, "
            "COALESCE(SUM(tokens_reasoning),0) AS tr, "
            "COALESCE(SUM(cost),0) AS cost "
            f"FROM usage_event WHERE user_id={p}"
        )
        with self._conn() as c, c.cursor() as cur:
            cur.execute(sql, (user_id,))
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
        p = self._ph()
        with self._conn() as c, c.cursor() as cur:
            cur.execute(f"DELETE FROM usage_event WHERE session_id={p}", (session_id,))
            cur.execute(f"DELETE FROM session_metadata WHERE id={p}", (session_id,))


def _json_value(value: Any, empty: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value) if value else empty
    return empty if value is None else value


def _optional_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if not value:
        return None
    return json.loads(value)


def _as_int(value: Any) -> int:
    return int(value or 0)


def _as_float(value: Any) -> float:
    return float(value or 0)


def _message_from_rows(mr: dict, parts_by_msg: Dict[str, List[dict]]) -> Message:
    data = _json_value(mr["message_data"], {})
    blocks = [block_from_dict(d) for d in parts_by_msg.get(mr["id"], [])]
    meta = dict(data.get("metadata") or {})
    meta["id"] = mr["id"]
    return Message(role=data.get("role", "user"), content=blocks, metadata=meta)


def _row_to_session(row: dict) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        directory=row["work_directory"],
        title=row["title"],
        agent=row.get("agent") or "build",
        user_id=row.get("user_id") or "local",
        model=_optional_json(row.get("model")),
        cost=_as_float(row.get("cost")),
        tokens_input=_as_int(row.get("tokens_input")),
        tokens_output=_as_int(row.get("tokens_output")),
        tokens_reasoning=_as_int(row.get("tokens_reasoning")),
        tokens_cache_read=_as_int(row.get("tokens_cache_read")),
        tokens_cache_write=_as_int(row.get("tokens_cache_write")),
        metadata=_json_value(row.get("metadata"), {}) or {},
        permission=_optional_json(row.get("permission")),
        time_created=_as_int(row.get("time_created")),
        time_updated=_as_int(row.get("time_updated")),
    )