"""File UX: SSE progress, session history attachments, upload limits."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.config import Config
from sleuth.files.ingest import reset_scheduler, wait_extracts
from sleuth.files.limits import files_limits_payload
from sleuth.files.mailbox import ingest_user_file
from sleuth.files.cos import MemoryObjectStore
from sleuth.messages import Message
from sleuth.progress import emit_ack, emit_progress
from sleuth.server.app import create_app
from sleuth.storage.base import SessionRecord
from sleuth.storage.sqlite import SQLiteStore


class LimitsPayloadTests(unittest.TestCase):
    def test_defaults(self):
        payload = files_limits_payload(Config())
        self.assertEqual(payload["max_bytes"], 52_428_800)
        self.assertEqual(payload["max_count"], 20)
        self.assertTrue(payload["mime_unrestricted"])
        self.assertEqual(payload["upload_form_field"], "file")
        self.assertIn(".pdf", payload["pdf_exts"])

    def test_http_limits_route(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(workdir=Path(td))
            from starlette.testclient import TestClient

            client = TestClient(app)
            res = client.get("/v1/files/limits")
            self.assertEqual(res.status_code, 200)
            body = res.json()["data"]
            self.assertIn("max_bytes", body)
            self.assertIn("max_count", body)


class ProgressEmitTests(unittest.TestCase):
    def test_ack_and_progress(self):
        events = []

        class R:
            def on_ack(self, **kwargs):
                events.append(("ack", kwargs))

            def on_progress(self, **kwargs):
                events.append(("progress", kwargs))

        sess = MagicMock()
        sess.renderer = R()
        sess.config = Config()
        emit_ack(sess)
        emit_progress(sess, stage="extract", file_id="file_1", detail="extracting")
        self.assertEqual(events[0][0], "ack")
        self.assertEqual(events[1][1]["stage"], "extract")
        self.assertEqual(events[1][1]["file_id"], "file_1")


class AgentProgressHelperTests(unittest.TestCase):
    def test_report_progress_calls_fastmcp_context(self):
        import importlib.util

        path = (
            Path(__file__).resolve().parents[1]
            / "agents"
            / "scaffold"
            / "optional"
            / "progress"
            / "progress.py"
        )
        spec = importlib.util.spec_from_file_location("sleuth_progress_helper", path)
        if spec is None or spec.loader is None:
            self.fail(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        seen = []

        class Ctx:
            def report_progress(self, current, total, message):
                seen.append((current, total, message))

        ctx = Ctx()
        mod.report_progress(ctx, "kb", detail="searching")
        fn = mod.bind_progress(ctx)
        self.assertIsNotNone(fn)
        fn("llm")
        self.assertEqual(seen[0][2], "kb: searching")
        self.assertEqual(seen[1][2], "llm")


class SessionHistoryFilesTests(unittest.TestCase):
    def test_get_session_returns_message_files(self):
        cfg = Config()
        cfg.files.require_encrypt = False
        mem = MemoryObjectStore()
        with tempfile.TemporaryDirectory() as td:
            store = SQLiteStore(Path(td) / "t.db")
            rec = SessionRecord(
                id="sess_histfiles000000000001",
                directory=str(td),
                title="t",
                user_id="alice",
                agent="build",
                metadata={},
            )
            store.create_session(rec)
            rec = store.get_session(rec.id)
            reset_scheduler()
            item = ingest_user_file(
                config=cfg,
                store=store,
                rec=rec,
                filename="notes.txt",
                mime="text/plain",
                data=b"hello",
                object_store=mem,
            )
            wait_extracts(2.0)
            rec = store.get_session(rec.id)
            store.save_message(
                rec.id,
                Message.user_text("这个文件说了什么", file_ids=[item["id"]]),
            )
            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ):
                app = create_app(workdir=Path(td))
                from starlette.testclient import TestClient

                client = TestClient(app)
                res = client.get(
                    f"/v1/sessions/{rec.id}",
                    headers={"X-User-Id": "alice"},
                )
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()["data"]
            self.assertTrue(body.get("files"))
            self.assertEqual(body["files"][0]["id"], item["id"])
            user = next(m for m in body["messages"] if m["role"] == "user")
            self.assertEqual(user["files"][0]["id"], item["id"])
            self.assertIn("download_url", user["files"][0])


if __name__ == "__main__":
    unittest.main()
