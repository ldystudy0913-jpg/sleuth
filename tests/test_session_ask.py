"""HTTP park/resume for the built-in question tool."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.config import AgentConfig, Config
from sleuth.messages import ToolResultBlock, ToolUseBlock
from sleuth.provider.base import Stop, TextDelta, ToolUse
from sleuth.session import NullRenderer, Session
from sleuth.tools.base import ToolResult


class QuestionParkTests(unittest.TestCase):
    def test_http_parks_question_then_resumes(self) -> None:
        class Provider:
            id = "openai"

            def __init__(self):
                self.n = 0

            def stream(self, **_kwargs):
                self.n += 1
                if self.n == 1:
                    yield TextDelta("还缺客户名称。")
                    yield ToolUse(
                        id="call_q",
                        name="question",
                        input={
                            "questions": [
                                {
                                    "header": "缺项确认",
                                    "question": (
                                        "当前还缺：客户名称。是否还有其他要补充的信息？"
                                    ),
                                    "options": [
                                        {
                                            "label": "补充信息 (Recommended)",
                                            "description": "",
                                        },
                                        {
                                            "label": "没有补充，继续分析",
                                            "description": "",
                                        },
                                    ],
                                    "custom": True,
                                }
                            ]
                        },
                    )
                    yield Stop("tool_use", usage={"input": 1, "output": 1})
                    return
                yield TextDelta("按现有信息继续分析。")
                yield Stop("end_turn", usage={"input": 1, "output": 1})

        registry = MagicMock()
        registry.specs.return_value = []
        registry.execute.return_value = ToolResult.success("ok", "should-not-run")
        registry.names.return_value = []

        cfg = Config(
            default_agent="build",
            agents={"build": AgentConfig(name="build")},
            max_steps=5,
        )
        sess = Session(
            provider=Provider(),
            registry=registry,
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            agent_name="build",
            model_id="m",
            renderer=NullRenderer(),
            store=None,
            title="ask",
            block_on_question=False,
        )
        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            first = sess.prompt("请生成答复框架 C001")
        self.assertEqual(sess.ask_payload()["status"], "awaiting_user")
        self.assertTrue(sess.ask_payload()["questions"])
        self.assertIn("客户名称", first)
        registry.execute.assert_not_called()

        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            second = sess.prompt("没有补充，继续")
        self.assertEqual(sess.ask_payload()["status"], "ok")
        self.assertIsNone(sess._pending_ask)
        self.assertIn("继续分析", second)
        tool_blocks = [
            b
            for m in sess.messages
            for b in m.content
            if isinstance(b, ToolResultBlock)
        ]
        self.assertTrue(any(b.tool_use_id == "call_q" for b in tool_blocks))
        self.assertTrue(
            any(isinstance(b, ToolUseBlock) and b.name == "question" for m in sess.messages for b in m.content)
        )


if __name__ == "__main__":
    unittest.main()
