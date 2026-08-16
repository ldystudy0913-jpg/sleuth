"""Session-pinned skill: selectors, CLI, HTTP, persistence."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.cli import _expand_command
from sleuth.config import AgentConfig, Config
from sleuth.permission import Permission
from sleuth.session import NullRenderer, Session
from sleuth.session_select import SKILL_ONLY_DEFAULT_ERROR, apply_session_selectors
from sleuth.skill import SkillInfo, set_skills
from sleuth.storage.base import SessionRecord
from sleuth.storage.sqlite import SQLiteStore
from sleuth.tools.registry import ToolRegistry


def _sess(**kwargs) -> Session:
    cfg = kwargs.pop("config", None) or Config(
        agents={
            "build": AgentConfig(name="build", description="default build"),
            "plan": AgentConfig(name="plan", description="planner"),
            "dd_analyst": AgentConfig(name="dd_analyst"),
        },
        default_agent="build",
        models={"fast": "openai/gpt-4o-mini"},
    )
    provider = MagicMock()
    provider.id = "openai"
    defaults = dict(
        provider=provider,
        registry=ToolRegistry(),
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


def _demo_skill() -> SkillInfo:
    return SkillInfo(
        name="demo",
        description="Demo skill",
        location=Path("/tmp/demo/SKILL.md"),
        content="# Demo\n\nDo the demo steps.",
    )


class SessionSkillTests(unittest.TestCase):
    def setUp(self):
        set_skills({"demo": _demo_skill()})
        self.addCleanup(lambda: set_skills({}))

    def test_set_skill_on_default_agent(self):
        sess = _sess()
        self.assertEqual(sess.set_skill("demo"), "demo")
        self.assertEqual(sess.skill_name, "demo")
        self.assertIsNone(sess.set_skill(""))
        self.assertIsNone(sess.skill_name)

    def test_set_skill_rejects_non_default_agent(self):
        sess = _sess(agent_name="plan")
        with self.assertRaises(ValueError) as ctx:
            sess.set_skill("demo")
        self.assertIn("default agent", str(ctx.exception))
        self.assertIsNone(sess.skill_name)

    def test_set_skill_unknown_name(self):
        sess = _sess()
        with self.assertRaises(ValueError) as ctx:
            sess.set_skill("nope")
        self.assertIn("unknown skill", str(ctx.exception))

    def test_set_agent_clears_skill(self):
        sess = _sess()
        sess.set_skill("demo")
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            sess.set_agent("plan")
        self.assertEqual(sess.agent_name, "plan")
        self.assertIsNone(sess.skill_name)

    def test_pinned_prompt_only_for_default_agent(self):
        sess = _sess(registry=ToolRegistry())
        sess.set_skill("demo")
        text = sess._pinned_skill_prompt()
        self.assertIn("Pinned skill", text)
        self.assertIn("Do the demo steps.", text)
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            sess.set_agent("plan")
            sess.skill_name = "demo"
        self.assertEqual(sess._pinned_skill_prompt(), "")

    def test_skill_sticky_restore(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_stickyskill000000000001"
            cfg = Config(
                default_agent="build",
                user_id="alice",
                agents={"build": AgentConfig(name="build")},
            )
            provider = MagicMock()
            provider.id = "p"
            sess = Session(
                provider=provider,
                registry=ToolRegistry(),
                config=cfg,
                workdir=Path(td),
                permission=Permission(rules=[]),
                agent_name="build",
                model_id="m",
                id=sid,
                renderer=NullRenderer(),
                store=store,
                user_id="alice",
            )
            sess.set_skill("demo")
            rec = store.get_session(sid)
            self.assertEqual(rec.metadata.get("skill"), "demo")

            sess2 = Session.load(
                provider=provider,
                registry=ToolRegistry(),
                config=cfg,
                workdir=Path(td),
                permission=Permission(rules=[]),
                store=store,
                session_id_value=sid,
                agent_name="build",
                model_id="m",
                renderer=NullRenderer(),
            )
            self.assertEqual(sess2.skill_name, "demo")


class ApplySelectorsTests(unittest.TestCase):
    def setUp(self):
        set_skills({"demo": _demo_skill()})
        self.addCleanup(lambda: set_skills({}))
        self.cfg = Config(
            default_agent="build",
            agents={
                "build": AgentConfig(name="build"),
                "dd_analyst": AgentConfig(name="dd_analyst"),
            },
        )

    def test_empty_skill_clears(self):
        sess = _sess()
        sess.skill_name = "demo"
        err = apply_session_selectors(sess, {"skill": ""}, self.cfg)
        self.assertIsNone(err)
        self.assertIsNone(sess.skill_name)

    def test_omit_skill_keeps_pin(self):
        sess = _sess()
        sess.skill_name = "demo"
        err = apply_session_selectors(sess, {"prompt": "hi"}, self.cfg)
        self.assertIsNone(err)
        self.assertEqual(sess.skill_name, "demo")

    def test_non_default_agent_rejects_skill(self):
        sess = _sess()
        err = apply_session_selectors(
            sess,
            {"agent": "dd_analyst", "skill": "demo"},
            self.cfg,
        )
        self.assertEqual(err, SKILL_ONLY_DEFAULT_ERROR)
        self.assertEqual(sess.agent_name, "build")
        self.assertIsNone(sess.skill_name)

    def test_switch_to_non_default_clears_skill(self):
        sess = _sess()
        sess.set_skill("demo")
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            err = apply_session_selectors(
                sess, {"agent": "dd_analyst", "skill": ""}, self.cfg
            )
        self.assertIsNone(err)
        self.assertEqual(sess.agent_name, "dd_analyst")
        self.assertIsNone(sess.skill_name)

    def test_null_is_omit(self):
        sess = _sess()
        sess.skill_name = "demo"
        err = apply_session_selectors(sess, {"skill": None, "agent": None}, self.cfg)
        self.assertIsNone(err)
        self.assertEqual(sess.skill_name, "demo")


class CliSkillSlashTests(unittest.TestCase):
    def setUp(self):
        set_skills({"demo": _demo_skill()})
        self.addCleanup(lambda: set_skills({}))

    def test_skill_bind_and_off(self):
        sess = _sess()
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            self.assertEqual(_expand_command(sess, "/skill demo"), (None, None))
            self.assertEqual(sess.skill_name, "demo")
            self.assertEqual(_expand_command(sess, "/skill off"), (None, None))
        self.assertIsNone(sess.skill_name)
        out = buf.getvalue()
        self.assertIn("skill set to demo", out)
        self.assertIn("skill cleared", out)

    def test_skill_rejects_when_not_default_agent(self):
        sess = _sess(agent_name="plan")
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            _expand_command(sess, "/skill demo")
        self.assertIsNone(sess.skill_name)
        self.assertIn("skill switch failed", buf.getvalue())

    def test_agent_switch_clears_and_notes(self):
        sess = _sess()
        sess.set_skill("demo")
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            with patch("sleuth.app.build_permission", return_value=MagicMock()):
                _expand_command(sess, "/agent plan")
        self.assertEqual(sess.agent_name, "plan")
        self.assertIsNone(sess.skill_name)
        self.assertIn("cleared skill", buf.getvalue())


class HttpSkillSelectorTests(unittest.TestCase):
    def test_create_and_message_skill_and_reject(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        from sleuth.server.app import create_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_httpskill000000000001"
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
                def __init__(self):
                    self.id = sid
                    self.user_id = "alice"
                    self.title = "t"
                    self.agent_name = "build"
                    self.model_id = "m"
                    self.provider = type("P", (), {"id": "p"})()
                    self.skill_name = None
                    self.yolo = True
                    self._last_usage = {}
                    self._session_cost = 0.0

                def model_ref(self):
                    return "p/m"

                def set_agent(self, name, yolo=False):
                    self.agent_name = name
                    if name != "build":
                        self.skill_name = None
                    return name

                def set_model(self, m):
                    self.model_id = str(m)
                    return m

                def reset_model(self):
                    self.model_id = "m"
                    return "p/m"

                def set_skill(self, name):
                    raw = (name or "").strip()
                    if raw.lower() in ("", "off", "none", "default"):
                        self.skill_name = None
                        return None
                    if self.agent_name != "build":
                        raise ValueError(
                            "skill only allowed when agent is the default agent"
                        )
                    self.skill_name = raw
                    return raw

                def _ensure_persisted(self):
                    return None

                def prompt(self, _prompt):
                    return "ok"

                def cancel(self):
                    return None

                def last_assistant_text(self):
                    return "ok"

            holder = {"sess": None}

            def fake_build(**_kwargs):
                sess = FakeSess()
                holder["sess"] = sess
                return sess

            cfg = Config(default_agent="build")
            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ), patch(
                "sleuth.server.app.build_session", side_effect=fake_build
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                headers = {
                    "X-User-Id": "alice",
                    "Content-Type": "application/json",
                }
                created = client.post(
                    "/v1/sessions",
                    headers=headers,
                    json={
                        "agent": "build",
                        "model": "m",
                        "skill": "demo",
                    },
                )
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json().get("skill"), "demo")

                ok = client.post(
                    f"/v1/sessions/{sid}/messages",
                    headers=headers,
                    json={
                        "prompt": "hi",
                        "agent": "build",
                        "model": "m",
                        "skill": "",
                    },
                )
                self.assertEqual(ok.status_code, 200)
                self.assertIsNone(ok.json().get("skill"))

                bad = client.post(
                    f"/v1/sessions/{sid}/messages",
                    headers=headers,
                    json={
                        "prompt": "hi",
                        "agent": "dd_analyst",
                        "model": "m",
                        "skill": "demo",
                    },
                )
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(bad.json().get("error"), SKILL_ONLY_DEFAULT_ERROR)


if __name__ == "__main__":
    unittest.main()
