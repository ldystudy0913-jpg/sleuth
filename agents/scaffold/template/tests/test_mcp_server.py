"""MCP registration tests (no live HTTP). Run after generate.py (imports __PKG_NAME__)."""
from __future__ import annotations

import json
import unittest

from __PKG_NAME__.agent_card import load_agent_card
from __PKG_NAME__.config import Settings
from __PKG_NAME__.mcp_server import build_mcp_server, health_payload, mcp_token_ok


class TestMcpServer(unittest.TestCase):
    def test_tools_registered(self) -> None:
        server = build_mcp_server(
            Settings(attachments_enabled=False, kb_enabled=False, output_enabled=False)
        )
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        for name in ("get_agent_card", "ping", "health"):
            self.assertIn(name, tools)
        self.assertNotIn("kb_search", tools)
        self.assertNotIn("emit_file", tools)

        health = json.loads(tools["health"].fn())
        self.assertTrue(health.get("ok"))
        self.assertTrue(health.get("agent_card"))

        pinged = json.loads(tools["ping"].fn(message="hello"))
        self.assertTrue(pinged.get("ok"))
        self.assertEqual(pinged.get("echo"), "hello")
        self.assertEqual(pinged.get("sources"), [])

        card = json.loads(tools["get_agent_card"].fn())
        self.assertEqual(card.get("name"), "__AGENT_NAME__")
        self.assertEqual(card.get("mcp_server"), "__SERVER_NAME__")
        self.assertIn("__SERVER_NAME___ping", card.get("permission") or {})
        self.assertEqual((card.get("permission") or {}).get("question"), "allow")
        self.assertIn("hitl", health)

    def test_http_health_payload(self) -> None:
        body = health_payload(Settings())
        self.assertTrue(body.get("ok"))
        self.assertIn("hitl", body)
        self.assertFalse(body.get("hitl"))

    def test_health_skips_mcp_token(self) -> None:
        self.assertTrue(mcp_token_ok("/health", "", "secret"))
        self.assertTrue(mcp_token_ok("/mcp", "", ""))
        self.assertFalse(mcp_token_ok("/mcp", "", "secret"))
        self.assertTrue(mcp_token_ok("/mcp", "Bearer secret", "secret"))


class TestAgentCard(unittest.TestCase):
    def test_local_skill_embedded(self) -> None:
        card = load_agent_card(server_name="__SERVER_NAME__")
        names = [s.get("name") for s in card.get("skills") or []]
        self.assertIn("__SKILL_SLUG__", names)
        private = next(s for s in card["skills"] if s["name"] == "__SKILL_SLUG__")
        self.assertTrue(private.get("content"))


if __name__ == "__main__":
    unittest.main()
