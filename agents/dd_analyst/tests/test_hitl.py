"""HITL start/resume + durable checkpoint / rollback tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dd_check.config import Settings
from dd_check.graph.checkpoint import apply_checkpoint_ddl
from dd_check.graph.runner import (
    list_checkpoints,
    reset_graphs,
    resume_check,
    rollback_check,
    start_check,
)
from dd_check.models import CheckRequest
from tests.test_orchestrator import _user_like_payload


class HitlTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_graphs()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.cp_path = Path(self._tmpdir.name) / "cp.sqlite3"
        apply_checkpoint_ddl(self.cp_path)

    def tearDown(self) -> None:
        reset_graphs()

    def _hitl_settings(self, **extra) -> Settings:
        return Settings(
            hitl_enabled=True,
            hitl_on_fail_only=False,
            checkpoint_sqlite_path=self.cp_path,
            **extra,
        )

    def test_hitl_off_completes(self):
        req = CheckRequest.model_validate(_user_like_payload())
        out = start_check(req, Settings(hitl_enabled=False))
        self.assertEqual(out.get("status"), "completed")
        self.assertIn("score", out)
        self.assertIn("findings", out)
        self.assertNotEqual(out.get("status"), "awaiting_human")

    def test_hitl_requires_checkpoint_path(self):
        req = CheckRequest.model_validate(_user_like_payload())
        with self.assertRaises(RuntimeError):
            start_check(req, Settings(hitl_enabled=True))

    def test_hitl_on_interrupt_then_approve(self):
        req = CheckRequest.model_validate(_user_like_payload())
        settings = self._hitl_settings()
        paused = start_check(req, settings)
        self.assertEqual(paused.get("status"), "awaiting_human")
        self.assertTrue(paused.get("thread_id"))
        self.assertIn("interrupt", paused)
        interrupt = paused["interrupt"]
        self.assertEqual(interrupt.get("type"), "dd_confirm")
        self.assertIn("findings_preview", interrupt)

        done = resume_check(
            paused["thread_id"],
            {"action": "approve"},
            settings,
        )
        self.assertEqual(done.get("status"), "completed")
        self.assertIn("score", done)
        self.assertEqual(
            (done.get("metadata") or {}).get("human_status"),
            "approved_by_human",
        )

    def test_hitl_reject(self):
        req = CheckRequest.model_validate(_user_like_payload())
        settings = self._hitl_settings()
        paused = start_check(req, settings)
        self.assertEqual(paused["status"], "awaiting_human")
        done = resume_check(
            paused["thread_id"],
            {"action": "reject", "feedback": "需补充说明"},
            settings,
        )
        self.assertEqual(done.get("status"), "rejected")
        self.assertIn("人工驳回", done.get("summary") or "")

    def test_hitl_edit_summary(self):
        req = CheckRequest.model_validate(_user_like_payload())
        settings = self._hitl_settings()
        paused = start_check(req, settings)
        done = resume_check(
            paused["thread_id"],
            {"action": "edit_summary", "summary": "人工改写摘要"},
            settings,
        )
        self.assertEqual(done.get("status"), "completed")
        self.assertEqual(done.get("summary"), "人工改写摘要")

    def test_hitl_survives_process_restart(self):
        """模拟 MCP 进程重启：reset_graphs 后仍能 resume。"""
        req = CheckRequest.model_validate(_user_like_payload())
        settings = self._hitl_settings()
        paused = start_check(req, settings)
        self.assertEqual(paused["status"], "awaiting_human")
        tid = paused["thread_id"]

        reset_graphs()  # drop in-process graph + connection

        done = resume_check(tid, {"action": "approve"}, settings)
        self.assertEqual(done.get("status"), "completed")
        self.assertEqual(
            (done.get("metadata") or {}).get("human_status"),
            "approved_by_human",
        )

    def test_list_and_rollback(self):
        req = CheckRequest.model_validate(_user_like_payload())
        settings = self._hitl_settings()
        paused = start_check(req, settings)
        tid = paused["thread_id"]
        listed = list_checkpoints(tid, settings)
        self.assertEqual(listed.get("status"), "ok")
        self.assertGreaterEqual(listed.get("count", 0), 1)
        checkpoints = listed["checkpoints"]
        # Prefer a checkpoint whose next step is before human_confirm if available
        target = None
        for row in reversed(checkpoints):  # older first among listed (list is newest first)
            nxt = row.get("next") or []
            if "score_aggregate" in nxt or "run_rule_dims" in nxt or "skip_attachments" in nxt:
                target = row.get("checkpoint_id")
                break
        if target is None:
            # fall back to oldest checkpoint
            target = checkpoints[-1].get("checkpoint_id")
        self.assertTrue(target)

        rolled = rollback_check(tid, target, settings)
        self.assertIn(rolled.get("status"), {"completed", "awaiting_human"})
        self.assertEqual(rolled.get("thread_id"), tid)

    def test_sync_with_checkpoint_returns_thread_id(self):
        req = CheckRequest.model_validate(_user_like_payload())
        settings = Settings(hitl_enabled=False, checkpoint_sqlite_path=self.cp_path)
        out = start_check(req, settings)
        self.assertEqual(out.get("status"), "completed")
        self.assertTrue(out.get("thread_id"))
        listed = list_checkpoints(out["thread_id"], settings)
        self.assertEqual(listed.get("status"), "ok")
        self.assertGreaterEqual(listed.get("count", 0), 1)


if __name__ == "__main__":
    unittest.main()
