"""CLI slash-command parity with HTTP catalog / sticky controls."""
from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.catalog import agents_payload, mcp_status_dict, models_payload
from sleuth.cli import _expand_command
from sleuth.config import AgentConfig, Config
from sleuth.session import Session


def _sess(**kwargs) -> Session:
    cfg = kwargs.pop("config", None) or Config(
        agents={
            "build": AgentConfig(name="build", description="default build"),
            "plan": AgentConfig(name="plan", description="planner"),
        },
        default_agent="build",
        models={"fast": "openai/gpt-4o-mini"},
    )
    provider = MagicMock()
    provider.id = "openai"
    defaults = dict(
        provider=provider,
        registry=MagicMock(),
        config=cfg,
        workdir=Path("."),
        permission=MagicMock(),
        agent_name="build",
        model_id="gpt-4o",
        store=None,
        yolo=False,
    )
    defaults.update(kwargs)
    return Session(**defaults)


class CatalogPayloadTests(unittest.TestCase):
    def test_models_payload_shape(self):
        cfg = Config(models={"fast": "openai/gpt-4o-mini"}, model="fast")
        payload = models_payload(cfg)
        self.assertEqual(payload["default"], "fast")
        self.assertEqual(payload["models"][0]["id"], "fast")
        self.assertIn("ref", payload["models"][0])

    def test_agents_payload_marks_mcp_availability(self):
        cfg = Config(
            agents={"build": AgentConfig(name="build")},
            default_agent="build",
        )
        mgr = MagicMock()
        mgr.agent_cards = {"dd_analyst": object()}
        mgr.agent_card_servers = {"dd_analyst": "dd"}
        mgr.is_server_connected.return_value = False
        payload = agents_payload(cfg, mcp_manager=mgr)
        by_name = {a["name"]: a for a in payload["agents"]}
        self.assertTrue(by_name["build"]["available"])
        self.assertEqual(by_name["build"]["source"], "local")
        self.assertEqual(by_name["build"]["title"], "通用助手")
        self.assertEqual(by_name["dd_analyst"]["title"], "dd_analyst")
        self.assertFalse(by_name["dd_analyst"]["available"])
        self.assertEqual(by_name["dd_analyst"]["source"], "mcp")
        self.assertEqual(by_name["dd_analyst"]["mcp_server"], "dd")

    def test_agents_payload_fills_card_after_hot_connect(self):
        cfg = Config(
            agents={"build": AgentConfig(name="build")},
            default_agent="build",
        )
        mgr = MagicMock()
        mgr.agent_cards = {
            "dd_analyst": {
                "name": "dd_analyst",
                "title": "尽调报告检查分析师",
                "description": "尽调报告检查",
                "mode": "primary",
                "prompt": "you are dd analyst",
            }
        }
        mgr.agent_card_servers = {"dd_analyst": "ddcheck"}
        mgr.is_server_connected.return_value = True
        payload = agents_payload(cfg, mcp_manager=mgr)
        row = {a["name"]: a for a in payload["agents"]}["dd_analyst"]
        self.assertTrue(row["available"])
        self.assertEqual(row["source"], "mcp")
        self.assertEqual(row["description"], "尽调报告检查")
        self.assertEqual(row["title"], "尽调报告检查分析师")
        self.assertEqual(cfg.agents["dd_analyst"].description, "尽调报告检查")
        self.assertEqual(cfg.agents["dd_analyst"].title, "尽调报告检查分析师")

    def test_mcp_status_dict_on_error(self):
        with patch("sleuth.catalog.get_manager", side_effect=RuntimeError("boom"), create=True):
            with patch("sleuth.mcp.get_manager", side_effect=RuntimeError("boom")):
                status = mcp_status_dict(Config())
        self.assertEqual(status["servers"], [])
        self.assertTrue(status["errors"])


class CliSlashTests(unittest.TestCase):
    def test_model_slash_returns_tuple(self):
        sess = _sess()
        fake = MagicMock()
        fake.id = "openai"
        with patch("sleuth.provider.factory.build_provider", return_value=fake):
            self.assertEqual(_expand_command(sess, "/model"), (None, None))
            self.assertEqual(_expand_command(sess, "/model fast"), (None, None))
        self.assertEqual(sess.model_id, "gpt-4o-mini")

    def test_agent_list_and_switch(self):
        sess = _sess()
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            with patch("sleuth.app.build_permission", return_value=MagicMock()):
                self.assertEqual(_expand_command(sess, "/agent"), (None, None))
                self.assertEqual(_expand_command(sess, "/agent plan"), (None, None))
        self.assertEqual(sess.agent_name, "plan")
        out = buf.getvalue()
        self.assertIn("current agent: build", out)
        self.assertIn("agent set to plan", out)

    def test_agent_warns_when_mcp_down(self):
        cfg = Config(
            agents={"build": AgentConfig(name="build")},
            default_agent="build",
        )
        sess = _sess(config=cfg)
        mgr = MagicMock()
        mgr.agent_cards = {"dd_analyst": object()}
        mgr.agent_card_servers = {"dd_analyst": "dd"}
        mgr.is_server_connected.return_value = False
        sess._mcp_manager = mgr
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            with patch("sleuth.app.build_permission", return_value=MagicMock()):
                _expand_command(sess, "/agent dd_analyst")
        self.assertEqual(sess.agent_name, "dd_analyst")
        self.assertIn("warning", buf.getvalue().lower())

    def test_agent_busy_rejects(self):
        sess = _sess()
        sess.status = "busy"
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            _expand_command(sess, "/agent plan")
        self.assertEqual(sess.agent_name, "build")
        self.assertIn("busy", buf.getvalue())

    def test_mcp_status_and_reload(self):
        sess = _sess()
        sess.registry = MagicMock()
        sess.registry._tools = {}
        sess._mcp_tool_names = set()
        status = {
            "servers": [
                {
                    "name": "dd",
                    "url": "http://x",
                    "connected": True,
                    "error": "",
                    "agent": "",
                    "agents": ["dd_analyst"],
                }
            ],
            "tools": ["dd_check"],
            "agents": ["dd_analyst"],
            "errors": [],
        }
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            with patch("sleuth.catalog.mcp_status_dict", return_value=status):
                self.assertEqual(_expand_command(sess, "/mcp"), (None, None))
            with patch(
                "sleuth.app.resync_session_mcp",
                return_value={
                    "ok": True,
                    "servers": status["servers"],
                    "tools": ["dd_check"],
                    "agents": ["dd_analyst"],
                    "errors": [],
                },
            ) as reload_mock:
                self.assertEqual(_expand_command(sess, "/mcp reload"), (None, None))
                reload_mock.assert_called_once_with(sess)
        out = buf.getvalue()
        self.assertIn("MCP servers", out)
        self.assertIn("mcp reloaded", out)

    def test_skills_list_and_reload(self):
        sess = _sess()
        rows = [
            {
                "name": "demo",
                "description": "Demo skill",
                "location": "/tmp/demo",
            }
        ]
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            with patch("sleuth.catalog.skills_payload", return_value=rows):
                self.assertEqual(_expand_command(sess, "/skills"), (None, None))
            with patch(
                "sleuth.app.reload_skills",
                return_value={"demo": object()},
            ) as reload_mock:
                self.assertEqual(_expand_command(sess, "/skills reload"), (None, None))
                reload_mock.assert_called_once()
        out = buf.getvalue()
        self.assertIn("demo", out)
        self.assertIn("skills reloaded", out)

    def test_usage_prints_sum(self):
        store = MagicMock()
        store.sum_usage.return_value = {
            "user_id": "alice",
            "events": 2,
            "tokens_input": 10,
            "tokens_output": 5,
            "tokens_reasoning": 1,
            "cost": 0.01,
        }
        sess = _sess(store=store, user_id="alice")
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            self.assertEqual(_expand_command(sess, "/usage"), (None, None))
        store.sum_usage.assert_called_once_with("alice")
        self.assertIn("tokens_input=10", buf.getvalue())

    def test_yolo_toggle(self):
        sess = _sess(yolo=False)
        with patch("sleuth.app.build_permission", return_value=MagicMock()) as bp:
            self.assertEqual(_expand_command(sess, "/yolo on"), (None, None))
            self.assertTrue(sess.yolo)
            bp.assert_called()
            self.assertEqual(_expand_command(sess, "/yolo off"), (None, None))
            self.assertFalse(sess.yolo)

    def test_custom_command_uses_set_agent(self):
        from sleuth.config import CommandConfig

        cfg = Config(
            agents={"build": AgentConfig(name="build"), "plan": AgentConfig(name="plan")},
            default_agent="build",
            commands={
                "review": CommandConfig(
                    name="review",
                    template="Review $ARGUMENTS",
                    agent="plan",
                )
            },
        )
        sess = _sess(config=cfg)
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            text, new = _expand_command(sess, "/review this")
        self.assertIsNone(new)
        self.assertEqual(text, "Review this")
        self.assertEqual(sess.agent_name, "plan")


if __name__ == "__main__":
    unittest.main()
