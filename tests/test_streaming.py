"""Tests for SSE StreamingRenderer and messages/stream route."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sleuth.config import AgentConfig, Config
from sleuth.catalog import agents_payload, models_payload
from sleuth.server.app import create_app
from sleuth.server.streaming import StreamingRenderer, sse_pack
from sleuth.storage.base import SessionRecord
from sleuth.tools.base import ToolResult


class StreamingRendererTests(unittest.TestCase):
    def test_event_order_and_session_id(self):
        sid = "sess_abc"
        r = StreamingRenderer(
            session_id=sid, args_max_chars=20, output_max_chars=20
        )
        r.on_step(1, 30)
        r.on_text("Hello")
        r.on_text(" world")
        r.on_tool_start("bash", {"cmd": "echo " + ("x" * 100)})
        r.on_tool_result(
            "bash",
            ToolResult(title="ok", output="y" * 100, is_error=False),
        )
        r.on_reasoning("think")
        r.on_error("boom")
        r.close()

        types = []
        while True:
            ev = r.get_event(timeout=0.1)
            if ev is None:
                break
            if ev.get("type") == "_poll":
                continue
            self.assertEqual(ev.get("session_id"), sid)
            types.append(ev["type"])
            if ev["type"] == "tool_start":
                self.assertLessEqual(len(ev["args_preview"]), 20)
                self.assertTrue(ev["args_preview"].endswith("..."))
            if ev["type"] == "tool_result":
                self.assertFalse(ev["is_error"])
                self.assertLessEqual(len(ev["output_preview"]), 20)

        self.assertEqual(
            types,
            [
                "step",
                "text",
                "text",
                "tool_start",
                "tool_result",
                "reasoning",
                "error",
            ],
        )

    def test_poll_then_done(self):
        r = StreamingRenderer(session_id="sess_x")
        poll = r.get_event(timeout=0.05)
        self.assertEqual(poll, {"type": "_poll"})
        r.close()
        self.assertIsNone(r.get_event(timeout=0.2))

    def test_sse_pack(self):
        raw = sse_pack({"type": "text", "delta": "你好", "session_id": "sess_1"})
        self.assertTrue(raw.startswith(b"data: "))
        self.assertTrue(raw.endswith(b"\n\n"))
        payload = json.loads(raw.decode("utf-8")[6:].strip())
        self.assertEqual(payload["delta"], "你好")
        self.assertEqual(payload["session_id"], "sess_1")


class CatalogPayloadTests(unittest.TestCase):
    def test_models_payload_no_secrets(self):
        cfg = Config(
            model="qwen-max",
            models={
                "qwen-max": {
                    "model": "qwen-max",
                    "apiKey": "sk-secret",
                    "baseURL": "https://example.com",
                },
                "ds": "deepseek/deepseek-chat",
            },
        )
        payload = models_payload(cfg)
        self.assertEqual(payload["default"], "qwen-max")
        ids = {m["id"] for m in payload["models"]}
        self.assertEqual(ids, {"ds", "qwen-max"})
        raw = json.dumps(payload)
        self.assertNotIn("sk-secret", raw)
        self.assertNotIn("apiKey", raw)
        qwen = next(m for m in payload["models"] if m["id"] == "qwen-max")
        self.assertEqual(qwen["ref"], "qwen-max/qwen-max")
        self.assertIn("example.com", qwen["label"])

    def test_agents_payload_filters_hidden(self):
        cfg = Config(
            default_agent="build",
            agents={
                "build": AgentConfig(name="build", description="default"),
                "dd_analyst": AgentConfig(name="dd_analyst", description="尽调"),
                "secret": AgentConfig(name="secret", hidden=True),
            },
        )
        visible = agents_payload(cfg, include_hidden=False)
        names = {a["name"] for a in visible["agents"]}
        self.assertEqual(names, {"build", "dd_analyst"})
        self.assertEqual(visible["default"], "build")
        all_agents = agents_payload(cfg, include_hidden=True)
        self.assertEqual(
            {a["name"] for a in all_agents["agents"]},
            {"build", "dd_analyst", "secret"},
        )
        for a in all_agents["agents"]:
            self.assertNotIn("prompt", a)


class StreamRouteTests(unittest.TestCase):
    def test_stream_route_emits_text_and_done(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        from sleuth.storage.sqlite import SQLiteStore

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db = Path(td) / "t.db"
            store = SQLiteStore(db)
            sid = "sess_streamtest000000000001"
            store.create_session(
                SessionRecord(
                    id=sid,
                    directory=td,
                    user_id="alice",
                    title="t",
                    agent="build",
                    model={"id": "m", "providerID": "p"},
                )
            )

            class FakeSess:
                def __init__(self, renderer):
                    self.id = sid
                    self.user_id = "alice"
                    self.title = "t"
                    self.agent_name = "build"
                    self.model_id = "m"
                    self.provider = type("P", (), {"id": "p"})()
                    self._last_usage = {
                        "input": 1,
                        "output": 2,
                        "reasoning": 0,
                        "cache_read": 0,
                        "cache_write": 0,
                    }
                    self._session_cost = 0.01
                    self._renderer = renderer
                    self._text = ""
                    self.cancelled = False
                    self.skill_name = None

                def model_ref(self):
                    return "p/m"

                def set_model(self, _m):
                    return None

                def set_agent(self, name, yolo=False):
                    self.agent_name = name
                    return name

                def set_skill(self, name):
                    raw = (name or "").strip()
                    self.skill_name = raw or None
                    return self.skill_name

                def reset_model(self):
                    return "p/m"

                def cancel(self):
                    self.cancelled = True

                def last_assistant_text(self):
                    return self._text

                def prompt(self, prompt: str) -> str:
                    time.sleep(0.05)
                    self._renderer.on_text("Hi")
                    self._renderer.on_text(" there")
                    self._text = "Hi there"
                    return self._text

            def fake_build_session(**kwargs):
                return FakeSess(kwargs["renderer"])

            cfg = Config()
            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ), patch(
                "sleuth.server.app.build_session", side_effect=fake_build_session
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                with client.stream(
                    "POST",
                    f"/v1/sessions/{sid}/messages/stream",
                    headers={
                        "X-User-Id": "alice",
                        "Content-Type": "application/json",
                    },
                    json={"prompt": "hello"},
                ) as res:
                    self.assertEqual(res.status_code, 200)
                    body = b"".join(res.iter_bytes())

            text = body.decode("utf-8")
            events = []
            for block in text.split("\n\n"):
                line = next(
                    (ln for ln in block.split("\n") if ln.startswith("data: ")),
                    None,
                )
                if not line:
                    continue
                events.append(json.loads(line[6:]))

            types = [e["type"] for e in events]
            self.assertIn("text", types)
            self.assertEqual(types[-1], "done")
            self.assertEqual(events[-1]["text"], "Hi there")
            for e in events:
                self.assertEqual(e.get("session_id"), sid)
            deltas = "".join(e["delta"] for e in events if e["type"] == "text")
            self.assertEqual(deltas, "Hi there")

    def test_stream_404_wrong_user(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        from sleuth.storage.sqlite import SQLiteStore

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_streamtest000000000002"
            store.create_session(
                SessionRecord(
                    id=sid,
                    directory=td,
                    user_id="alice",
                    title="t",
                    agent="build",
                    model={},
                )
            )
            cfg = Config()
            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                res = client.post(
                    f"/v1/sessions/{sid}/messages/stream",
                    headers={
                        "X-User-Id": "bob",
                        "Content-Type": "application/json",
                    },
                    json={"prompt": "hello"},
                )
                self.assertEqual(res.status_code, 404)

    def test_models_and_agents_routes(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        from sleuth.storage.sqlite import SQLiteStore

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            cfg = Config(
                model="qwen-max",
                default_agent="build",
                models={"qwen-max": {"model": "qwen-max", "apiKey": "sk-x"}},
                agents={
                    "build": AgentConfig(name="build"),
                    "dd_analyst": AgentConfig(
                        name="dd_analyst",
                        title="尽调报告检查分析师",
                        description="尽调",
                    ),
                },
            )
            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                m = client.get("/v1/models")
                self.assertEqual(m.status_code, 200)
                body = m.json()
                self.assertEqual(body["default"], "qwen-max")
                self.assertEqual(body["models"][0]["id"], "qwen-max")
                self.assertNotIn("apiKey", json.dumps(body))

                a = client.get("/v1/agents")
                self.assertEqual(a.status_code, 200)
                agents = a.json()
                self.assertEqual(agents["default"], "build")
                self.assertEqual(
                    {x["name"] for x in agents["agents"]},
                    {"build", "dd_analyst"},
                )
                by_name = {x["name"]: x for x in agents["agents"]}
                self.assertEqual(by_name["dd_analyst"]["title"], "尽调报告检查分析师")
                self.assertEqual(by_name["build"]["title"], "通用助手")


if __name__ == "__main__":
    unittest.main()
