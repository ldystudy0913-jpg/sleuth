"""Session execution trace projection, loop timing, and HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.config import AgentConfig, Config
from sleuth.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from sleuth.provider.base import Stop, TextDelta, ToolUse
from sleuth.session import NullRenderer, Session
from sleuth.storage.base import SessionRecord
from sleuth.storage.sqlite import SQLiteStore
from sleuth.tools.base import ToolResult
from sleuth.trace import project_session_trace


class ProjectTraceTests(unittest.TestCase):
    def test_order_and_timing(self):
        user = Message.user_text("请检查报告", started_at=1000)
        user.metadata["id"] = "msg_u"
        assistant = Message.assistant(
            [
                TextBlock("正在调用工具"),
                ToolUseBlock(id="call_1", name="ddreply_generate_reply_framework", input={}),
            ],
            step=1,
            started_at=1100,
            first_token_at=1250,
            completed_at=2000,
            duration_ms=900,
            usage={"input": 10, "output": 4},
        )
        assistant.metadata["id"] = "msg_a"
        tools = Message.tool_results(
            [ToolResultBlock(tool_use_id="call_1", content="ok", is_error=False)],
            tool_spans=[
                {
                    "id": "call_1",
                    "name": "ddreply_generate_reply_framework",
                    "started_at": 2000,
                    "duration_ms": 800,
                    "ended_at": 2800,
                    "is_error": False,
                }
            ],
        )
        tools.metadata["id"] = "msg_t"
        payload = project_session_trace(
            [user, assistant, tools], session_id="sess_1"
        )
        kinds = [r["kind"] for r in payload["records"]]
        self.assertEqual(kinds, ["user", "message", "tool"])
        self.assertEqual(payload["session_id"], "sess_1")
        self.assertEqual(payload["records"][1]["first_token_at"], 1250)
        self.assertEqual(payload["records"][1]["duration_ms"], 900)
        self.assertEqual(payload["records"][2]["id"], "call_1")
        self.assertEqual(payload["records"][2]["duration_ms"], 800)
        self.assertFalse(payload["records"][2]["is_error"])

    def test_legacy_messages_null_timing(self):
        user = Message.user_text("hello")
        assistant = Message.assistant([TextBlock("world")])
        payload = project_session_trace([user, assistant], session_id="s")
        self.assertIsNone(payload["records"][0]["started_at"])
        self.assertIsNone(payload["records"][1]["duration_ms"])
        self.assertIsNone(payload["records"][1]["step"])

    def test_tool_without_spans_uses_block(self):
        assistant = Message.assistant(
            [ToolUseBlock(id="c1", name="bash", input={"cmd": "x"})]
        )
        tools = Message.tool_results(
            [ToolResultBlock(tool_use_id="c1", content="out", is_error=True)]
        )
        payload = project_session_trace([assistant, tools], session_id="s")
        tool = payload["records"][1]
        self.assertEqual(tool["kind"], "tool")
        self.assertEqual(tool["name"], "bash")
        self.assertTrue(tool["is_error"])
        self.assertIsNone(tool["duration_ms"])


class LoopTimingTests(unittest.TestCase):
    def test_prompt_writes_started_at_and_tool_span(self):
        class FakeProvider:
            id = "openai"

            def stream(self, **_kwargs):
                yield TextDelta("hi")
                yield ToolUse(id="call_9", name="skill", input={"name": "demo"})
                yield Stop("tool_use", usage={"input": 2, "output": 1})

        class OnceThenStop(FakeProvider):
            def __init__(self):
                self.n = 0

            def stream(self, **_kwargs):
                self.n += 1
                if self.n == 1:
                    yield from FakeProvider.stream(self)
                    return
                yield TextDelta("done")
                yield Stop("end_turn", usage={"input": 1, "output": 1})

        registry = MagicMock()
        registry.specs.return_value = []
        registry.execute.return_value = ToolResult.success("ok", "loaded")
        registry.names.return_value = []

        cfg = Config(
            default_agent="build",
            agents={"build": AgentConfig(name="build")},
            max_steps=5,
        )
        sess = Session(
            provider=OnceThenStop(),
            registry=registry,
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            agent_name="build",
            model_id="m",
            renderer=NullRenderer(),
            store=None,
            title="loop-timing",
        )
        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            sess.prompt("hello")

        user = sess.messages[0]
        self.assertEqual(user.role, "user")
        self.assertIsInstance(user.metadata.get("started_at"), int)
        assistant = sess.messages[1]
        self.assertEqual(assistant.metadata.get("step"), 1)
        self.assertIsInstance(assistant.metadata.get("started_at"), int)
        self.assertIsInstance(assistant.metadata.get("first_token_at"), int)
        self.assertGreaterEqual(
            assistant.metadata.get("completed_at"),
            assistant.metadata.get("first_token_at"),
        )
        tool_msg = sess.messages[2]
        spans = tool_msg.metadata.get("tool_spans") or []
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["id"], "call_9")
        self.assertIn("duration_ms", spans[0])


class TraceHttpTests(unittest.TestCase):
    def test_get_trace_and_session_timing(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        from sleuth.server.app import create_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_tracetest0000000000001"
            store.create_session(
                SessionRecord(
                    id=sid,
                    directory=td,
                    user_id="alice",
                    title="t",
                    agent="build",
                )
            )
            user = Message.user_text("hi", started_at=10)
            assistant = Message.assistant(
                [TextBlock("yo")],
                step=1,
                started_at=20,
                first_token_at=25,
                completed_at=40,
                duration_ms=20,
                usage={"input": 1, "output": 1},
            )
            store.save_message(sid, user)
            store.save_message(sid, assistant)

            cfg = Config(default_agent="build")
            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                headers = {"X-User-Id": "alice"}
                tr = client.get(f"/v1/sessions/{sid}/trace", headers=headers)
                self.assertEqual(tr.status_code, 200)
                body = tr.json()
                self.assertEqual(body["session_id"], sid)
                self.assertEqual([r["kind"] for r in body["records"]], ["user", "message"])
                self.assertEqual(body["records"][1]["duration_ms"], 20)

                detail = client.get(f"/v1/sessions/{sid}", headers=headers)
                self.assertEqual(detail.status_code, 200)
                msgs = detail.json()["messages"]
                self.assertEqual(msgs[1]["step"], 1)
                self.assertEqual(msgs[1]["started_at"], 20)

                missing = client.get(
                    f"/v1/sessions/{sid}/trace",
                    headers={"X-User-Id": "bob"},
                )
                self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
