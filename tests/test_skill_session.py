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
from sleuth.session_select import (
    SKILL_ONLY_DEFAULT_ERROR,
    apply_session_selectors,
    parse_skill_names,
    skills_from_metadata,
)
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


def _other_skill() -> SkillInfo:
    return SkillInfo(
        name="other",
        description="Other skill",
        location=Path("/tmp/other/SKILL.md"),
        content="# Other\n\nDo the other steps.",
    )


class SessionSkillTests(unittest.TestCase):
    def setUp(self):
        set_skills({"demo": _demo_skill()})
        self.addCleanup(lambda: set_skills({}))

    def test_set_skill_on_default_agent(self):
        sess = _sess()
        self.assertEqual(sess.set_skill("demo"), "demo")
        self.assertEqual(sess.skill_name, "demo")
        self.assertEqual(sess.skill_names, ["demo"])
        self.assertIsNone(sess.set_skill(""))
        self.assertIsNone(sess.skill_name)
        self.assertEqual(sess.skill_names, [])

    def test_set_skills_multiple(self):
        set_skills({"demo": _demo_skill(), "other": _other_skill()})
        sess = _sess()
        self.assertEqual(sess.set_skills(["demo", "other", "demo"]), ["demo", "other"])
        self.assertEqual(sess.skill_name, "demo")
        self.assertEqual(sess.skill_names, ["demo", "other"])
        text = sess._pinned_skill_prompt()
        self.assertIn("Do the demo steps.", text)
        self.assertIn("Do the other steps.", text)
        self.assertIn("these names", text)

    def test_add_and_remove_skill(self):
        set_skills({"demo": _demo_skill(), "other": _other_skill()})
        sess = _sess()
        sess.set_skill("demo")
        self.assertEqual(sess.add_skill("other"), ["demo", "other"])
        self.assertEqual(sess.remove_skill("demo"), ["other"])
        self.assertEqual(sess.remove_skill("missing"), ["other"])
        self.assertEqual(sess.remove_skill("other"), [])

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
            self.assertEqual(rec.metadata.get("skills"), ["demo"])

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
            self.assertEqual(sess2.skill_names, ["demo"])

    def test_skill_sticky_restore_list(self):
        set_skills({"demo": _demo_skill(), "other": _other_skill()})
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_stickyskills00000000002"
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
            sess.set_skills(["other", "demo"])
            rec = store.get_session(sid)
            self.assertEqual(rec.metadata.get("skill"), "other")
            self.assertEqual(rec.metadata.get("skills"), ["other", "demo"])

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
            self.assertEqual(sess2.skill_names, ["other", "demo"])


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

    def test_parse_and_metadata(self):
        self.assertEqual(parse_skill_names("demo, other demo"), ["demo", "other"])
        self.assertEqual(parse_skill_names([" other ", "demo", "off"]), ["other", "demo"])
        self.assertEqual(
            skills_from_metadata({"skill": "demo", "skills": ["other", "demo"]}),
            ["other", "demo"],
        )
        self.assertEqual(skills_from_metadata({"skill": "demo"}), ["demo"])

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

    def test_skills_array_sets_multiple(self):
        set_skills({"demo": _demo_skill(), "other": _other_skill()})
        sess = _sess()
        err = apply_session_selectors(
            sess, {"skills": ["other", "demo", "other"]}, self.cfg
        )
        self.assertIsNone(err)
        self.assertEqual(sess.skill_names, ["other", "demo"])

    def test_skills_wins_over_skill(self):
        set_skills({"demo": _demo_skill(), "other": _other_skill()})
        sess = _sess()
        sess.set_skill("demo")
        err = apply_session_selectors(
            sess, {"skill": "demo", "skills": ["other"]}, self.cfg
        )
        self.assertIsNone(err)
        self.assertEqual(sess.skill_names, ["other"])

    def test_empty_skills_clears(self):
        sess = _sess()
        sess.set_skill("demo")
        err = apply_session_selectors(sess, {"skills": []}, self.cfg)
        self.assertIsNone(err)
        self.assertEqual(sess.skill_names, [])

    def test_non_default_rejects_skills_array(self):
        sess = _sess()
        err = apply_session_selectors(
            sess,
            {"agent": "dd_analyst", "skills": ["demo"]},
            self.cfg,
        )
        self.assertEqual(err, SKILL_ONLY_DEFAULT_ERROR)
        self.assertEqual(sess.agent_name, "build")


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

    def test_skill_multi_and_add(self):
        set_skills({"demo": _demo_skill(), "other": _other_skill()})
        sess = _sess()
        buf = io.StringIO()
        with patch("sleuth.cli._print", side_effect=lambda s: buf.write(s)):
            self.assertEqual(_expand_command(sess, "/skill demo other"), (None, None))
            self.assertEqual(sess.skill_names, ["demo", "other"])
            self.assertEqual(_expand_command(sess, "/skill -demo"), (None, None))
            self.assertEqual(sess.skill_names, ["other"])
            self.assertEqual(_expand_command(sess, "/skill +demo"), (None, None))
            self.assertEqual(sess.skill_names, ["other", "demo"])
        out = buf.getvalue()
        self.assertIn("skills set to demo, other", out)

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
                    self.skill_names = []
                    self.yolo = True
                    self._last_usage = {}
                    self._session_cost = 0.0

                @property
                def skill_name(self):
                    return self.skill_names[0] if self.skill_names else None

                @skill_name.setter
                def skill_name(self, value):
                    raw = (value or "").strip() if isinstance(value, str) else ""
                    self.skill_names = [raw] if raw else []

                def model_ref(self):
                    return "p/m"

                def set_agent(self, name, yolo=False):
                    self.agent_name = name
                    if name != "build":
                        self.skill_names = []
                    return name

                def set_model(self, m):
                    self.model_id = str(m)
                    return m

                def reset_model(self):
                    self.model_id = "m"
                    return "p/m"

                def set_skills(self, names):
                    parsed = parse_skill_names(names)
                    if not parsed:
                        self.skill_names = []
                        return []
                    if self.agent_name != "build":
                        raise ValueError(
                            "skill only allowed when agent is the default agent"
                        )
                    self.skill_names = parsed
                    return list(self.skill_names)

                def set_skill(self, name):
                    names = self.set_skills([name] if name else [])
                    return names[0] if names else None

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
                self.assertEqual(created.json().get("skills"), ["demo"])

                multi = client.post(
                    "/v1/sessions",
                    headers=headers,
                    json={
                        "agent": "build",
                        "model": "m",
                        "skills": ["demo", "other"],
                    },
                )
                self.assertEqual(multi.status_code, 200)
                self.assertEqual(multi.json().get("skill"), "demo")
                self.assertEqual(multi.json().get("skills"), ["demo", "other"])

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
                self.assertEqual(ok.json().get("skills"), [])

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
