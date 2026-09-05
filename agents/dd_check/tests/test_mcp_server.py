"""MCP registration tests (no live HTTP)."""
from __future__ import annotations

import json
import unittest

from dd_check.agent_card import load_agent_card
from dd_check.config import Settings
from dd_check.mcp_server import build_mcp_server, health_payload, mcp_token_ok


class TestMcpServer(unittest.TestCase):
    def test_tools_registered(self) -> None:
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not installed")
        server = build_mcp_server(
            Settings(attachments_enabled=False, kb_enabled=False, output_enabled=False)
        )
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        for name in ("get_agent_card", "check_report", "health"):
            self.assertIn(name, tools)
        self.assertNotIn("ping", tools)
        self.assertNotIn("kb_search", tools)
        self.assertNotIn("emit_file", tools)

        health = json.loads(tools["health"].fn())
        self.assertTrue(health.get("ok"))
        self.assertTrue(health.get("agent_card"))

        checked = json.loads(tools["check_report"].fn(report_text="demo"))
        self.assertIn("ok", checked)
        self.assertFalse(checked.get("ok"))

        card = json.loads(tools["get_agent_card"].fn())
        self.assertEqual(card.get("name"), "dd_check")
        self.assertEqual(card.get("mcp_server"), "ddcheck")
        self.assertIn("ddcheck_check_report", card.get("permission") or {})
        self.assertEqual((card.get("permission") or {}).get("question"), "allow")

    def test_hitl_need_input_when_empty(self) -> None:
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not installed")
        server = build_mcp_server(
            Settings(
                attachments_enabled=False,
                kb_enabled=False,
                output_enabled=False,
                hitl_enabled=True,
            )
        )
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        health = json.loads(tools["health"].fn())
        self.assertTrue(health.get("hitl"))
        paused = json.loads(tools["check_report"].fn())
        self.assertEqual(paused.get("status"), "need_input")
        missing = paused.get("missing") or []
        self.assertIn("报告正文 report_text", missing)
        continued = json.loads(tools["check_report"].fn(proceed_with_gaps=True))
        self.assertIn("ok", continued)
        self.assertNotEqual(continued.get("status"), "need_input")
        with_text = json.loads(tools["check_report"].fn(report_text="demo"))
        self.assertNotEqual(with_text.get("status"), "need_input")

    def test_hitl_skips_when_excerpt(self) -> None:
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not installed")
        server = build_mcp_server(
            Settings(
                attachments_enabled=True,
                kb_enabled=False,
                output_enabled=False,
                hitl_enabled=True,
            )
        )
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        refs = json.dumps(
            [{"filename": "a.pdf", "excerpt": "客户名称示例"}],
            ensure_ascii=False,
        )
        body = json.loads(tools["check_report"].fn(attachment_refs_json=refs))
        self.assertNotEqual(body.get("status"), "need_input")

    def test_http_health_payload(self) -> None:
        body = health_payload(Settings())
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("service"), "dd_check-tools")
        self.assertIn("hitl", body)

    def test_health_skips_mcp_token(self) -> None:
        self.assertTrue(mcp_token_ok("/health", "", "secret"))
        self.assertTrue(mcp_token_ok("/mcp", "", ""))
        self.assertFalse(mcp_token_ok("/mcp", "", "secret"))
        self.assertTrue(mcp_token_ok("/mcp", "Bearer secret", "secret"))


class TestAgentCard(unittest.TestCase):
    def test_local_skill_embedded(self) -> None:
        card = load_agent_card(server_name="ddcheck")
        names = [s.get("name") for s in card.get("skills") or []]
        self.assertIn("dd-check-sop", names)
        self.assertIn("ddcheck_check_report", card.get("permission") or {})
        self.assertEqual((card.get("permission") or {}).get("question"), "allow")


if __name__ == "__main__":
    unittest.main()
