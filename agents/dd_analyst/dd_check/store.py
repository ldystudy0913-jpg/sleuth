"""检查结果落库（sqlite 或内存）。与 sleuth Store 无关。

SQLite 表 dd_check_result 须由运维预先建好（见 deploy/ddl_dd_check_result.sql）；
本模块不做 CREATE TABLE。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .config import Settings
from .models import CheckResult


class ResultStore:
    def save(self, result: CheckResult) -> str:
        raise NotImplementedError

    def get(self, result_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError


class MemoryResultStore(ResultStore):
    """未配置 DD_CHECK_SQLITE_PATH 时使用，不依赖任何表。"""

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save(self, result: CheckResult) -> str:
        rid = uuid.uuid4().hex
        with self._lock:
            self._rows[rid] = {
                "id": rid,
                "saved_at": time.time(),
                "result": result.model_dump(),
            }
        return rid

    def get(self, result_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._rows.get(result_id)
            return dict(row) if row else None

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = sorted(self._rows.values(), key=lambda r: r["saved_at"], reverse=True)
            return [dict(r) for r in rows[:limit]]


class SqliteResultStore(ResultStore):
    """读写已存在的 dd_check_result 表（不建表）。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, result: CheckResult) -> str:
        rid = uuid.uuid4().hex
        payload = json.dumps(result.model_dump(), ensure_ascii=False)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO dd_check_result(id, report_id, invest_id, phase, score, grade, payload, saved_at)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        rid,
                        result.reportId,
                        result.investId,
                        result.phase,
                        result.score,
                        result.grade,
                        payload,
                        time.time(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return rid

    def get(self, result_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT id, saved_at, payload FROM dd_check_result WHERE id=?",
                    (result_id,),
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return {
            "id": row["id"],
            "saved_at": row["saved_at"],
            "result": json.loads(row["payload"]),
        }

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, saved_at, payload FROM dd_check_result ORDER BY saved_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "saved_at": row["saved_at"],
                    "result": json.loads(row["payload"]),
                }
            )
        return out


def build_store(settings: Settings) -> ResultStore:
    if settings.sqlite_path:
        return SqliteResultStore(str(settings.sqlite_path))
    return MemoryResultStore()
