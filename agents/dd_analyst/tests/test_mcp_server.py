"""Unit tests for MCP tool registration (no live HTTP required)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dd_check.config import Settings
from dd_check.graph.checkpoint import apply_checkpoint_ddl
from dd_check.graph.runner import reset_graphs
from dd_check.mcp_server import build_mcp_server
from tests.test_orchestrator import _user_like_payload


class TestMcpServer(unittest.TestCase):
    def setUp(self) -> None:
        reset_graphs()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.cp_path = Path(self._tmpdir.name) / "cp.sqlite3"
        apply_checkpoint_ddl(self.cp_path)

    def tearDown(self) -> None:
        reset_graphs()

    def test_tools_registered_and_run_dd_check(self) -> None:
        server = build_mcp_server(Settings(hitl_enabled=False))
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        self.assertIn("run_dd_check", tools)
        self.assertIn("resume_dd_check", tools)
        self.assertIn("list_dd_checkpoints", tools)
        self.assertIn("rollback_dd_check", tools)
        self.assertIn("get_agent_card", tools)
        self.assertIn("run_dd_batch", tools)
        self.assertIn("describe_graph", tools)
        self.assertIn("health", tools)

        card = json.loads(tools["get_agent_card"].fn())
        self.assertEqual(card.get("name"), "dd_analyst")
        self.assertEqual(card.get("title"), "尽调报告检查分析师")
        self.assertTrue(card.get("prompt"))
        self.assertIn("ddcheck_run_dd_check", card.get("permission") or {})
        self.assertIn("ddcheck_list_dd_checkpoints", card.get("permission") or {})
        self.assertIn("ddcheck_rollback_dd_check", card.get("permission") or {})

        payload = _user_like_payload()
        text = tools["run_dd_check"].fn(
            reportId=payload["reportId"],
            investId=payload.get("investId", ""),
            result=payload["result"],
            question=payload.get("question", ""),
            busCode=payload.get("busCode", ""),
            busCodeDesc=payload.get("busCodeDesc", ""),
            currentDateTime=payload.get("currentDateTime", ""),
            custType=payload.get("custType", ""),
            approveData=payload.get("approveData", ""),
            phase=payload.get("phase", "CHECK"),
            bankId=payload.get("bankId", ""),
        )
        data = json.loads(text)
        self.assertEqual(data.get("status"), "completed")
        self.assertIn("score", data)
        self.assertIn("findings", data)
        self.assertIn("trace", data)
        self.assertGreaterEqual(len(data["findings"]), 1)

    def test_mcp_hitl_run_resume(self) -> None:
        settings = Settings(
            hitl_enabled=True,
            checkpoint_sqlite_path=self.cp_path,
        )
        server = build_mcp_server(settings)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        payload = _user_like_payload()
        paused = json.loads(
            tools["run_dd_check"].fn(
                reportId=payload["reportId"],
                result=payload["result"],
                custType=payload.get("custType", ""),
                phase=payload.get("phase", "CHECK"),
                question=payload.get("question", ""),
                busCode=payload.get("busCode", ""),
                currentDateTime=payload.get("currentDateTime", ""),
                bankId=payload.get("bankId", ""),
            )
        )
        self.assertEqual(paused["status"], "awaiting_human")
        listed = json.loads(tools["list_dd_checkpoints"].fn(thread_id=paused["thread_id"]))
        self.assertEqual(listed.get("status"), "ok")
        self.assertGreaterEqual(listed.get("count", 0), 1)
        done = json.loads(
            tools["resume_dd_check"].fn(
                thread_id=paused["thread_id"],
                decision_json=json.dumps({"action": "approve"}),
            )
        )
        self.assertEqual(done["status"], "completed")

    def test_health_tool(self) -> None:
        server = build_mcp_server(
            Settings(hitl_enabled=True, checkpoint_sqlite_path=self.cp_path)
        )
        tool = server._tool_manager.get_tool("health")
        assert tool is not None
        data = json.loads(tool.fn())
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("hitl_enabled"))
        self.assertTrue(data.get("checkpoint_sqlite_configured"))
        self.assertTrue(data.get("agent_card"))


if __name__ == "__main__":
    unittest.main()
