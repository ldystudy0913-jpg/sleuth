"""In-memory history store: insert two runs, FIFO trim, delete."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from dd_check.history import HistoryStore


class _FakeCursor:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db
        self._rows = []
        self.description = None

    def execute(self, sql: str, args=()):
        text = " ".join((sql or "").split())
        low = text.lower()
        if low.startswith("insert"):
            row = {
                "check_id": args[0],
                "report_id": args[1],
                "scenario": args[2],
                "scenario_title": args[3],
                "filename": args[4],
                "content_type": args[5],
                "object_key": args[6],
                "created_at": args[7],
                "updated_at": args[8],
            }
            self._db.rows.append(row)
            self.description = None
            self._rows = []
            return
        if low.startswith("select") and "where report_id" in low:
            rid = args[0]
            matched = [r for r in self._db.rows if r["report_id"] == rid]
            matched.sort(key=lambda r: r["created_at"], reverse=True)
            self._rows = matched
            self.description = ("check_id",)
            return
        if low.startswith("select") and "where check_id" in low:
            cid = args[0]
            self._rows = [r for r in self._db.rows if r["check_id"] == cid]
            self.description = ("check_id",)
            return
        if low.startswith("delete"):
            cid = args[0]
            self._db.rows = [r for r in self._db.rows if r["check_id"] != cid]
            self.description = None
            self._rows = []
            return
        raise AssertionError(sql)

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db

    def cursor(self):
        return _FakeCursor(self._db)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeDb:
    def __init__(self) -> None:
        self.rows = []


class _MemStore(HistoryStore):
    def __init__(self) -> None:
        self._settings = None
        self._use_log_trace = False
        self._MySql = None
        self._dict_cursor = None
        self._pymysql = None
        self._db = _FakeDb()

    def _ph(self) -> str:
        return "%s"

    def _conn(self):
        return _FakeConn(self._db)


class HistoryStoreTests(unittest.TestCase):
    def test_two_runs_same_report_id(self) -> None:
        store = _MemStore()
        t0 = datetime(2026, 9, 1, 12, 0, 0)
        a = store.insert_run(
            report_id="R1",
            scenario="default",
            scenario_title="default",
            filename="a.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            object_key="k/a.docx",
            check_id="c1",
            created_at=t0,
        )
        b = store.insert_run(
            report_id="R1",
            scenario="corp_change",
            scenario_title="change",
            filename="b.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            object_key="k/b.docx",
            check_id="c2",
            created_at=t0 + timedelta(minutes=1),
        )
        self.assertEqual(a, "c1")
        self.assertEqual(b, "c2")
        rows = store.list_by_report("R1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["check_id"], "c2")

    def test_trim_keeps_latest_n(self) -> None:
        store = _MemStore()
        t0 = datetime(2026, 9, 1, 12, 0, 0)
        for i in range(3):
            store.insert_run(
                report_id="R1",
                scenario="default",
                scenario_title="d",
                filename=f"{i}.docx",
                content_type="x",
                object_key=f"k/{i}",
                check_id=f"c{i}",
                created_at=t0 + timedelta(minutes=i),
            )
        dropped = store.trim_report("R1", 2)
        self.assertEqual([d["check_id"] for d in dropped], ["c0"])
        left = store.list_by_report("R1")
        self.assertEqual([r["check_id"] for r in left], ["c2", "c1"])

    def test_delete_removes_row(self) -> None:
        store = _MemStore()
        t0 = datetime(2026, 9, 1, 12, 0, 0)
        store.insert_run(
            report_id="R1",
            scenario="default",
            scenario_title="d",
            filename="a.docx",
            content_type="x",
            object_key="k/a",
            check_id="c1",
            created_at=t0,
        )
        store.delete("c1")
        self.assertEqual(store.list_by_report("R1"), [])


if __name__ == "__main__":
    unittest.main()
