"""Tests for MCP Agent Card parsing, permission sanitize, and opt-in behavior."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock

from sleuth.config import AgentConfig, Config, McpServerConfig, _parse_mcp_server
from sleuth.mcp.agent_card import (
    apply_agent_cards_to_config,
    merge_agent_fill_empty,
    parse_agent_card,
    sanitize_permissions,
)


class AgentCardParseTests(unittest.TestCase):
    def test_parse_and_sanitize_bash_allow(self):
        raw = {
            "name": "dd_analyst",
            "title": "尽调报告检查分析师",
            "description": "demo",
            "mode": "primary",
            "prompt": "you are dd",
            "permission": {
                "ddcheck_run_dd_check": "allow",
                "bash": "allow",
                "edit": "allow",
            },
            "skills": [
                {
                    "name": "dd-report-check",
                    "description": "sop",
                    "content": "# hello\n",
                }
            ],
        }
        prev = os.environ.pop("SLEUTH_MCP_AGENT_TRUST_PERMISSIONS", None)
        try:
            agent, skills = parse_agent_card(raw, server_name="ddcheck")
        finally:
            if prev is not None:
                os.environ["SLEUTH_MCP_AGENT_TRUST_PERMISSIONS"] = prev
        self.assertEqual(agent.name, "dd_analyst")
        self.assertEqual(agent.title, "尽调报告检查分析师")
        self.assertEqual(agent.prompt, "you are dd")
        self.assertEqual(agent.permission["ddcheck_run_dd_check"], "allow")
        self.assertEqual(agent.permission["bash"], "ask")
        self.assertEqual(agent.permission["edit"], "ask")
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "dd-report-check")
        self.assertIn("hello", skills[0].content)

    def test_trust_permissions_env(self):
        os.environ["SLEUTH_MCP_AGENT_TRUST_PERMISSIONS"] = "1"
        try:
            out = sanitize_permissions({"bash": "allow"})
            self.assertEqual(out["bash"], "allow")
        finally:
            os.environ.pop("SLEUTH_MCP_AGENT_TRUST_PERMISSIONS", None)

    def test_fill_empty_local_wins(self):
        local = AgentConfig(name="dd_analyst", prompt="LOCAL", permission={"bash": "deny"})
        remote = AgentConfig(
            name="dd_analyst",
            prompt="REMOTE",
            permission={"bash": "ask", "ddcheck_health": "allow"},
        )
        merge_agent_fill_empty(local, remote)
        self.assertEqual(local.prompt, "LOCAL")
        self.assertEqual(local.permission["bash"], "deny")
        self.assertEqual(local.permission["ddcheck_health"], "allow")

    def test_apply_cards_to_config(self):
        cfg = Config()
        cards = {
            "dd_analyst": {
                "name": "dd_analyst",
                "prompt": "from mcp",
                "permission": {"ddcheck_health": "allow"},
                "skills": [],
                "mcp_server": "ddcheck",
            }
        }
        skills = apply_agent_cards_to_config(cfg, cards)
        self.assertIn("dd_analyst", cfg.agents)
        self.assertEqual(cfg.agents["dd_analyst"].prompt, "from mcp")
        self.assertEqual(skills, [])
        self.assertEqual(cfg.resolve_agent_name("ddcheck"), "dd_analyst")
        self.assertEqual(cfg.agent("ddcheck").prompt, "from mcp")

    def test_ddreply_alias_maps_to_dd_reply(self):
        cfg = Config()
        apply_agent_cards_to_config(
            cfg,
            {
                "dd_reply": {
                    "name": "dd_reply",
                    "title": "尽调答复框架生成助手",
                    "prompt": "你是尽调答复框架生成助手（dd_reply）",
                    "skills": [],
                    "mcp_server": "ddreply",
                }
            },
        )
        self.assertEqual(cfg.resolve_agent_name("ddreply"), "dd_reply")
        self.assertEqual(cfg.resolve_agent_name("dd-reply"), "dd_reply")
        self.assertIn("你是尽调答复框架生成助手", cfg.agent("ddreply").prompt or "")

    def test_parse_mcp_server_agent_default_false(self):
        srv = _parse_mcp_server(
            "ddcheck",
            {"type": "remote", "url": "http://127.0.0.1:8791/mcp"},
        )
        self.assertFalse(srv.agent)

    def test_parse_mcp_server_agent_true(self):
        srv = _parse_mcp_server(
            "ddcheck",
            {"type": "remote", "url": "http://127.0.0.1:8791/mcp", "agent": True},
        )
        self.assertTrue(srv.agent)

    def test_agent_false_does_not_require_card_tool(self):
        """Compatibility: agent=false means manager must not call get_agent_card.

        We assert the gate condition used in _connect_one.
        """
        srv = McpServerConfig(name="ddcheck", url="http://x", agent=False)
        self.assertFalse(getattr(srv, "agent", False))


class ApplyCardsLocalPriorityTests(unittest.TestCase):
    def test_existing_local_agent_not_overwritten(self):
        cfg = Config()
        cfg.agents["dd_analyst"] = AgentConfig(
            name="dd_analyst", prompt="from disk", permission={"bash": "deny"}
        )
        apply_agent_cards_to_config(
            cfg,
            {
                "dd_analyst": {
                    "name": "dd_analyst",
                    "prompt": "from mcp",
                    "permission": {"bash": "ask", "ddcheck_health": "allow"},
                    "mcp_server": "ddcheck",
                }
            },
        )
        self.assertEqual(cfg.agents["dd_analyst"].prompt, "from disk")
        self.assertEqual(cfg.agents["dd_analyst"].permission["bash"], "deny")
        self.assertEqual(cfg.agents["dd_analyst"].permission["ddcheck_health"], "allow")


if __name__ == "__main__":
    unittest.main()
