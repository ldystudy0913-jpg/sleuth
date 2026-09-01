"""MCP registration tests (no live HTTP). Run after generate.py (imports __PKG_NAME__)."""
from __future__ import annotations

import json
import unittest

from __PKG_NAME__.agent_card import COS_SKILL, PRIVATE_SKILL, SKILL_MODE, load_agent_card
from __PKG_NAME__.config import Settings
from __PKG_NAME__.mcp_server import build_mcp_server, health_payload


class TestMcpServer(unittest.TestCase):
    def test_tools_registered(self) -> None:
        server = build_mcp_server(Settings())
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        for name in ("get_agent_card", "ping", "health"):
            self.assertIn(name, tools)

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

    def test_http_health_payload(self) -> None:
        body = health_payload(Settings())
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("service"), "__PKG_NAME__-tools")


class TestAgentCard(unittest.TestCase):
    def test_skill_mode_matches_card(self) -> None:
        card = load_agent_card(server_name="__SERVER_NAME__")
        names = [s.get("name") for s in card.get("skills") or []]
        mode = (SKILL_MODE or "").strip().lower()
        if mode in ("private", "both"):
            self.assertIn(PRIVATE_SKILL, names)
            private = next(s for s in card["skills"] if s["name"] == PRIVATE_SKILL)
            self.assertTrue(private.get("content"))
        if mode in ("cos", "both"):
            self.assertIn(COS_SKILL, names)
            shared = next(s for s in card["skills"] if s["name"] == COS_SKILL)
            self.assertNotIn("content", shared)
        if mode == "none":
            self.assertEqual(card.get("skills"), [])


if __name__ == "__main__":
    unittest.main()
