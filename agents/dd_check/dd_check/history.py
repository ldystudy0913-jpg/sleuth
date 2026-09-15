"""Check-run pointer store. INSERT/SELECT/DELETE only; never CREATE TABLE."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from .bizerror import APPError, BizErrorCode
from .config import Settings
from .objects import WORD_CONTENT_TYPE, history_object_key

TABLE = "dd_check_check_run"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HistoryStore:
    def __init__(
        self,
        settings: Settings,
        *,
        use_log_trace: bool = False,
    ) -> None:
        self._settings = settings
        self._use_log_trace = bool(use_log_trace)
        self._MySql = None
        self._dict_cursor = None
        self._pymysql = None
        if self._use_log_trace:
            from log_trace.mysql_log_trace import DictCursor, MySql

            self._MySql = MySql
            self._dict_cursor = DictCursor
            return
        try:
            import pymysql
        except ImportError as exc:
            raise APPError.of(
                BizErrorCode.ABNORMAL_OPERATION,
                "PyMySQL is required for history",
            ) from exc
        self._pymysql = pymysql

    def _ph(self) -> str:
        return "?" if self._use_log_trace else "%s"

    def _conn(self):
        if self._use_log_trace:
            from .logtrace import config_file

            return self._MySql(
                config_path=config_file(),
                autocommit=True,
                cursorclass=self._dict_cursor,
            )
        s = self._settings
        return self._pymysql.connect(
            host=s.mysql_host,
            port=int(s.mysql_port),
            user=s.mysql_user,
            password=s.mysql_password,
            database=s.mysql_database,
            charset="utf8mb4",
            cursorclass=self._pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _run(self, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(args))
                    rows = cur.fetchall() if getattr(cur, "description", None) else []
        except APPError:
            raise
        except Exception as exc:
            raise APPError.of(BizErrorCode.ABNORMAL_OPERATION, str(exc)) from exc
        if not rows:
            return []
        return [dict(row) for row in rows]

    def _exec(self, sql: str, args: Sequence[Any], fail: BizErrorCode) -> None:
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(args))
        except APPError:
            raise
        except Exception as exc:
            raise APPError.of(fail, str(exc)) from exc

    def insert_run(
        self,
        *,
        report_id: str,
        scenario: str,
        scenario_title: str,
        filename: str,
        content_type: str,
        object_key: str,
        check_id: str = "",
        created_at: Optional[datetime] = None,
    ) -> str:
        cid = (check_id or "").strip() or str(uuid4())
        when = created_at or _now()
        ph = self._ph()
        sql = (
            f"INSERT INTO {TABLE} (check_id, report_id, scenario, scenario_title, "
            f"filename, content_type, object_key, created_at, updated_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"
        )
        self._exec(
            sql,
            (
                cid,
                report_id,
                scenario,
                scenario_title,
                filename,
                content_type or WORD_CONTENT_TYPE,
                object_key,
                when,
                when,
            ),
            BizErrorCode.INSERT_FAIL,
        )
        return cid

    def list_by_report(self, report_id: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        sql = (
            f"SELECT check_id, report_id, scenario, scenario_title, filename, "
            f"content_type, object_key, created_at, updated_at FROM {TABLE} "
            f"WHERE report_id = {ph} ORDER BY created_at DESC"
        )
        return self._run(sql, (report_id,))

    def get(self, check_id: str) -> Optional[Dict[str, Any]]:
        ph = self._ph()
        sql = (
            f"SELECT check_id, report_id, scenario, scenario_title, filename, "
            f"content_type, object_key, created_at, updated_at FROM {TABLE} "
            f"WHERE check_id = {ph}"
        )
        rows = self._run(sql, (check_id,))
        return rows[0] if rows else None

    def delete(self, check_id: str) -> Optional[Dict[str, Any]]:
        row = self.get(check_id)
        if row is None:
            return None
        ph = self._ph()
        self._exec(
            f"DELETE FROM {TABLE} WHERE check_id = {ph}",
            (check_id,),
            BizErrorCode.UPDATE_FAIL,
        )
        return row

    def trim_report(self, report_id: str, max_keep: int) -> List[Dict[str, Any]]:
        cap = int(max_keep or 0)
        if cap <= 0:
            return []
        rows = self.list_by_report(report_id)
        extra = rows[cap:]
        dropped: List[Dict[str, Any]] = []
        for row in extra:
            deleted = self.delete(str(row.get("check_id") or ""))
            if deleted:
                dropped.append(deleted)
        return dropped


def history_enabled(settings: Settings) -> bool:
    try:
        from .logtrace import mysql_enabled
    except ImportError:
        mysql_enabled = lambda: False  # type: ignore
    if mysql_enabled():
        return True
    return bool(
        settings.mysql_host
        and settings.mysql_user
        and settings.mysql_database
    )


def build_history_store(settings: Settings) -> Optional[HistoryStore]:
    if not history_enabled(settings):
        return None
    try:
        from .logtrace import mysql_enabled
    except ImportError:
        use_lt = False
    else:
        use_lt = bool(mysql_enabled())
    return HistoryStore(settings, use_log_trace=use_lt)


def persist_check(
    settings: Settings,
    *,
    report_id: str,
    scenario: str,
    scenario_title: str,
    filename: str,
    content_type: str,
    docx_bytes: bytes,
    history: Optional[HistoryStore] = None,
    objects=None,
) -> Dict[str, Any]:
    rid = (report_id or "").strip()
    if not rid:
        return {"ok": False, "detail": "report_id empty"}
    store = history if history is not None else build_history_store(settings)
    obj = objects if objects is not None else None
    if obj is None:
        from .objects import build_object_store

        obj = build_object_store(settings)
    if store is None or obj is None:
        return {"ok": False, "detail": "history store or COS not configured"}
    check_id = str(uuid4())
    key = history_object_key(settings.cos_path_prefix, rid, check_id)
    obj.put_bytes(key=key, data=docx_bytes, content_type=content_type or WORD_CONTENT_TYPE)
    store.insert_run(
        report_id=rid,
        scenario=scenario,
        scenario_title=scenario_title,
        filename=filename,
        content_type=content_type or WORD_CONTENT_TYPE,
        object_key=key,
        check_id=check_id,
    )
    dropped = store.trim_report(rid, int(settings.history_max or 10))
    for old in dropped:
        old_key = str(old.get("object_key") or "").strip()
        if old_key:
            try:
                obj.delete_object(old_key)
            except APPError:
                pass
    return {"ok": True, "check_id": check_id, "object_key": key}
