"""Tests for orchestration mode resolution and guarded invoke."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from sleuth.config import AgentConfig, Config, OrchestrationConfig
from sleuth.mcp.agent_card import merge_agent_fill_empty, parse_agent_card
from sleuth.orchestration import (
    agent_delegatable,
    build_auto_invoke_args,
    message_requires_prompt,
    resolve_orchestration,
    try_orchestrated_turn,
)
from sleuth.tools.base import ToolContext, ToolResult


class OrchestrationConfigTests(unittest.TestCase):
    def test_parse_card_orchestration_fields(self):
        raw = {
            "name": "dd_check",
            "orchestration": "pipeline",
            "primary_tool": "ddcheck_check_report",
            "delegatable": False,
            "execution": "sync",
            "auto_invoke_prompt_field": "report_text",
            "auto_invoke_args": {"question": ""},
        }
        agent, _skills = parse_agent_card(raw, server_name="ddcheck")
        self.assertEqual(agent.orchestration, "pipeline")
        self.assertEqual(agent.primary_tool, "ddcheck_check_report")
        self.assertFalse(agent.delegatable)
        self.assertEqual(agent.execution, "sync")
        self.assertEqual(agent.auto_invoke_prompt_field, "report_text")
        self.assertIn("question", agent.auto_invoke_args)

    def test_resolve_priority_request_over_card(self):
        cfg = Config(
            orchestration=OrchestrationConfig(default_mode="host"),
            agents={
                "dd_check": AgentConfig(
                    name="dd_check",
                    orchestration="pipeline",
                )
            },
        )
        session = MagicMock()
        session.config = cfg
        session.agent_name = "dd_check"
        session.store = None
        mode = resolve_orchestration(session, {"orchestration": "delegate"})
        self.assertEqual(mode, "delegate")

    def test_message_requires_prompt_invoke(self):
        cfg = Config(orchestration=OrchestrationConfig())
        self.assertFalse(message_requires_prompt({"invoke": {"tool": "x"}}, cfg))
        self.assertTrue(message_requires_prompt({"prompt": "hi"}, cfg))

    def test_build_auto_invoke_args_uses_agent_field(self):
        cfg = Config(
            agents={
                "dd_check": AgentConfig(
                    name="dd_check",
                    auto_invoke_prompt_field="report_text",
                    auto_invoke_args={"question": "q"},
                )
            }
        )
        session = MagicMock()
        session.config = cfg
        session.agent_name = "dd_check"
        args = build_auto_invoke_args(session, "正文", {})
        self.assertEqual(args["report_text"], "正文")
        self.assertEqual(args["question"], "q")

    def test_delegatable_defaults_from_config(self):
        cfg = Config(
            orchestration=OrchestrationConfig(default_delegatable=False),
            agents={"dd_check": AgentConfig(name="dd_check")},
        )
        self.assertFalse(agent_delegatable(cfg, "dd_check"))


class GuardedInvokeTests(unittest.TestCase):
    def test_auto_run_invokes_primary_tool(self):
        cfg = Config(
            orchestration=OrchestrationConfig(auto_run_enabled=True),
            agents={
                "dd_check": AgentConfig(
                    name="dd_check",
                    primary_tool="ddcheck_check_report",
                    auto_invoke_prompt_field="report_text",
                    permission={"ddcheck_check_report": "allow"},
                )
            },
        )

        class _Registry:
            def execute(self, name, args, ctx):
                self.last = (name, args, ctx)
                return ToolResult.success(name, json.dumps({"score": 88, "findings": []}))

        session = MagicMock()
        session.config = cfg
        session.agent_name = "dd_check"
        session.user_id = "alice"
        session.store = None
        session._turn_sources = []
        session._append_sources_footer = lambda t: t
        session._harvest_turn_sources = lambda _r: None
        registry = _Registry()
        session.registry = registry

        def _exec(tool_name, args):
            ctx = ToolContext(workdir=Path("."), session=session)
            return registry.execute(tool_name, args, ctx)

        session.execute_guarded_tool = _exec

        turn = try_orchestrated_turn(session, {"auto_run": True}, "检查报告")
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertIn("88", turn.text)
        self.assertEqual(registry.last[0], "ddcheck_check_report")
        self.assertEqual(registry.last[1]["report_text"], "检查报告")

    def test_delegate_blocked_when_not_delegatable(self):
        from sleuth.tools.task import TaskTool

        cfg = Config(
            orchestration=OrchestrationConfig(delegate_enabled=True, default_delegatable=False),
            agents={"dd_check": AgentConfig(name="dd_check", delegatable=False)},
        )
        parent = MagicMock()
        parent.config = cfg
        parent.user_id = "alice"
        parent.permission = MagicMock()
        parent.permission.ask_fn = None
        parent.store = None
        parent.workdir = Path(".")
        parent.provider = MagicMock()
        parent.model_id = "m"
        parent.registry = MagicMock()
        parent.id = "parent"

        tool = TaskTool()
        ctx = ToolContext(workdir=Path("."), session=parent)
        ctx.permission = MagicMock()
        result = tool.execute(
            {
                "description": "check",
                "prompt": "go",
                "subagent_type": "dd_check",
            },
            ctx,
        )
        self.assertTrue(result.is_error)
        self.assertIn("delegatable", result.output.lower())


if __name__ == "__main__":
    unittest.main()
