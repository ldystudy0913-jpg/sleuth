"""Tests for Beijing-local session titles and session list helpers."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sleuth.messages import Message, TextBlock
from sleuth.session_browse import (
    build_session_list_rows,
    first_user_preview,
    resolve_session_id,
    truncate_preview,
)
from sleuth.storage.sqlite import SQLiteStore
from sleuth.storage.base import SessionRecord
from sleuth.title import default_title, format_local_ms, is_default_title


class TitleTimezoneTests(unittest.TestCase):
    def test_default_title_local_no_z(self):
        with mock.patch.dict(os.environ, {"SLEUTH_TIMEZONE": "Asia/Shanghai"}):
            t = default_title()
        self.assertTrue(t.startswith("New session - "))
        self.assertFalse(t.endswith("Z"))
        self.assertNotIn("T", t.split(" - ", 1)[-1])
        self.assertTrue(is_default_title(t))

    def test_is_default_title_accepts_legacy_utc(self):
        legacy = "New session - 2026-08-08T10:09:12.123Z"
        self.assertTrue(is_default_title(legacy))
        self.assertTrue(is_default_title("Child session - 2026-08-08T10:09:12.123Z"))

    def test_is_default_title_rejects_custom(self):
        self.assertFalse(is_default_title("尽调报告检查"))

    def test_format_local_ms_shanghai(self):
        # 2024-01-01 00:00:00 UTC → 08:00:00 Asia/Shanghai
        with mock.patch.dict(os.environ, {"SLEUTH_TIMEZONE": "Asia/Shanghai"}):
            s = format_local_ms(1704067200000)
        self.assertEqual(s, "2024-01-01 08:00:00")


class SessionBrowseTests(unittest.TestCase):
    def test_truncate_preview(self):
        self.assertEqual(truncate_preview("hello"), "hello")
        self.assertEqual(truncate_preview("a" * 100, max_chars=10), "aaaaaaa...")

    def test_first_user_preview(self):
        msgs = [
            Message.assistant([TextBlock(text="hi")]),
            Message.user_text("  check this report please  "),
        ]
        self.assertEqual(first_user_preview(msgs), "check this report please")

    def test_build_rows_and_resolve(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            store.create_session(
                SessionRecord(
                    id="sess_aaa111",
                    directory=str(td),
                    title="New session - 2026-08-08 18:00:00",
                    agent="build",
                    user_id="alice",
                )
            )
            store.save_message(
                "sess_aaa111",
                Message.user_text("please review the due diligence report for bank X"),
            )
            store.create_session(
                SessionRecord(
                    id="sess_bbb222",
                    directory=str(td),
                    title="other",
                    agent="build",
                    user_id="alice",
                )
            )
            rows = build_session_list_rows(store, user_id="alice", limit=10)
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0]["time_updated_local"])
            # newest first — bbb was created later
            self.assertEqual(rows[0]["id"], "sess_bbb222")
            self.assertEqual(rows[1]["preview"][:20], "please review the du")

            self.assertEqual(resolve_session_id(rows, "2"), "sess_aaa111")
            self.assertEqual(resolve_session_id(rows, "sess_aaa111"), "sess_aaa111")
            self.assertEqual(
                resolve_session_id(rows, "sess_aaa", store=store, user_id="alice"),
                "sess_aaa111",
            )


if __name__ == "__main__":
    unittest.main()
