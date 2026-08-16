"""MCP tool registration tests (no live HTTP)."""
from __future__ import annotations

import json
import unittest

from dd_reply.config import Settings
from dd_reply.mcp_server import build_mcp_server


class TestMcpServer(unittest.TestCase):
    def test_tools_registered(self) -> None:
        server = build_mcp_server(Settings())
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        for name in (
            "get_agent_card",
            "generate_reply_framework",
            "lookup_risk_kb",
            "list_risk_codes",
            "list_lexicon",
            "health",
        ):
            self.assertIn(name, tools)

        health = json.loads(tools["health"].fn())
        self.assertTrue(health.get("ok"))
        self.assertGreaterEqual(health.get("risk_point_count", 0), 8)

        codes = json.loads(tools["list_risk_codes"].fn())
        self.assertIn("C001", codes.get("codes") or [])

        looked = json.loads(tools["lookup_risk_kb"].fn(codes_json='["C001","C999"]'))
        self.assertEqual(len(looked.get("found") or []), 1)
        self.assertEqual(looked.get("missing"), ["C999"])

        card = json.loads(tools["get_agent_card"].fn())
        self.assertEqual(card.get("name"), "dd_reply")
        self.assertEqual(card.get("title"), "尽调答复框架生成助手")
        self.assertTrue(card.get("skills"))

        out = json.loads(
            tools["generate_reply_framework"].fn(
                risk_codes_json='["C005"]',
                customer_name="乙公司",
            )
        )
        self.assertIn("markdown", out)
        self.assertIn("待核实", out["markdown"])


if __name__ == "__main__":
    unittest.main()
