"""Agent identity: aliases, custom prompt, not the underlying model."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.config import AgentConfig, Config
from sleuth.prompts import assemble
from sleuth.session import Session
from sleuth.session_select import apply_session_selectors


class ResolveAgentNameTests(unittest.TestCase):
    def test_punctuation_variant(self):
        cfg = Config(
            agents={"dd_reply": AgentConfig(name="dd_reply", prompt="REPLY")},
        )
        self.assertEqual(cfg.resolve_agent_name("ddreply"), "dd_reply")
        self.assertEqual(cfg.resolve_agent_name("dd-reply"), "dd_reply")
        self.assertEqual(cfg.agent("ddreply").prompt, "REPLY")


class AssembleIdentityTests(unittest.TestCase):
    def test_custom_agent_skips_default_sleuth_prompt(self):
        cfg = Config(
            agents={
                "dd_reply": AgentConfig(
                    name="dd_reply",
                    title="尽调答复框架生成助手",
                    prompt="你是尽调答复框架生成助手（dd_reply）",
                )
            },
            guardrails=False,
        )
        text = assemble(
            workdir=Path("."),
            config=cfg,
            agent_name="ddreply",
            model="Qwen3.6-35B-A3B",
            tool_specs=[],
            guardrails=False,
        )
        self.assertIn("尽调答复框架生成助手", text)
        self.assertIn("agent `dd_reply`", text)
        self.assertNotIn("You are sleuth", text)
        self.assertIn("Underlying model (not your identity)", text)
        self.assertIn("Qwen3.6-35B-A3B", text)
        self.assertIn("Never identify as the underlying model", text)

    def test_default_agent_keeps_sleuth_base(self):
        text = assemble(
            workdir=Path("."),
            config=Config(guardrails=False),
            agent_name="build",
            model="Qwen3.6-35B-A3B",
            tool_specs=[],
            guardrails=False,
        )
        self.assertIn("You are sleuth", text)
        self.assertIn("Never identify as the underlying model", text)


class SetAgentAliasTests(unittest.TestCase):
    def test_set_agent_persists_canonical_name(self):
        cfg = Config(
            agents={"dd_reply": AgentConfig(name="dd_reply", prompt="REPLY")},
            default_agent="build",
        )
        sess = Session(
            provider=MagicMock(),
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            agent_name="build",
            store=None,
        )
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            self.assertEqual(sess.set_agent("ddreply"), "dd_reply")
        self.assertEqual(sess.agent_name, "dd_reply")

    def test_http_body_agent_ddreply(self):
        cfg = Config(
            agents={
                "build": AgentConfig(name="build"),
                "dd_reply": AgentConfig(name="dd_reply", prompt="REPLY"),
            },
            default_agent="build",
        )
        sess = Session(
            provider=MagicMock(),
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            agent_name="build",
            store=None,
        )
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            err = apply_session_selectors(
                sess, {"agent": "ddreply", "text": "你是谁"}, cfg
            )
        self.assertIsNone(err)
        self.assertEqual(sess.agent_name, "dd_reply")


if __name__ == "__main__":
    unittest.main()
