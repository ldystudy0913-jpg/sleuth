"""Sticky agent/model session restore tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.app import build_session
from sleuth.config import Config
from sleuth.permission import Permission
from sleuth.session import NullRenderer, Session
from sleuth.storage.base import SessionRecord
from sleuth.storage.sqlite import SQLiteStore
from sleuth.tools.registry import ToolRegistry


class StickySessionTests(unittest.TestCase):
    def test_prefer_agent_overrides_and_persists(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_stickyagent000000000001"
            store.create_session(
                SessionRecord(
                    id=sid,
                    directory=td,
                    title="t",
                    agent="build",
                    user_id="alice",
                    model={"id": "m", "providerID": "p", "ref": "p/m"},
                )
            )
            cfg = Config(default_agent="build", user_id="alice")
            provider = MagicMock()
            provider.id = "p"
            sess = Session.load(
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
                prefer_agent="dd_analyst",
            )
            self.assertEqual(sess.agent_name, "dd_analyst")
            sess.set_agent("dd_analyst")
            rec = store.get_session(sid)
            self.assertEqual(rec.agent, "dd_analyst")

            # Second load without prefer keeps sticky agent
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
                prefer_agent=None,
            )
            self.assertEqual(sess2.agent_name, "dd_analyst")

    def test_model_sticky_restores_catalog_credentials(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = SQLiteStore(Path(td) / "t.db")
            sid = "sess_stickymodel000000000001"
            cfg = Config(
                model="default-model",
                models={
                    "qwen-max": {
                        "model": "qwen-max",
                        "apiKey": "sk-qw",
                        "baseURL": "https://dashscope.example/v1",
                    }
                },
                user_id="alice",
            )
            store.create_session(
                SessionRecord(
                    id=sid,
                    directory=td,
                    title="t",
                    agent="build",
                    user_id="alice",
                    model={},
                )
            )
            provider = MagicMock()
            provider.id = "openai"
            sess = Session(
                provider=provider,
                registry=ToolRegistry(),
                config=cfg,
                workdir=Path(td),
                permission=Permission(rules=[]),
                agent_name="build",
                model_id="default-model",
                id=sid,
                renderer=NullRenderer(),
                store=store,
                user_id="alice",
            )
            with patch("sleuth.provider.factory.build_provider") as bp:
                fake = MagicMock()
                fake.id = "qwen-max"
                bp.return_value = fake
                ref = sess.set_model("qwen-max")
                self.assertIn("qwen-max", ref)
                rec = store.get_session(sid)
                self.assertEqual(rec.model.get("key"), "qwen-max")
                self.assertTrue(rec.model.get("ref"))

            # Fresh config (as HTTP does each request) must still restore via prepare_model_ref
            cfg2 = Config(
                model="default-model",
                models={
                    "qwen-max": {
                        "model": "qwen-max",
                        "apiKey": "sk-qw",
                        "baseURL": "https://dashscope.example/v1",
                    }
                },
                user_id="alice",
            )
            provider2 = MagicMock()
            provider2.id = "openai"
            with patch("sleuth.provider.factory.build_provider") as bp2:
                fake2 = MagicMock()
                fake2.id = "qwen-max"
                bp2.return_value = fake2
                sess2 = Session.load(
                    provider=provider2,
                    registry=ToolRegistry(),
                    config=cfg2,
                    workdir=Path(td),
                    permission=Permission(rules=[]),
                    store=store,
                    session_id_value=sid,
                    agent_name="build",
                    model_id="default-model",
                    renderer=NullRenderer(),
                )
                bp2.assert_called()
                # prepare_model_ref should have seeded options
                opts = cfg2.provider_options("qwen-max")
                self.assertEqual(opts.get("apiKey"), "sk-qw")
                self.assertIn("dashscope.example", opts.get("baseURL", ""))
                self.assertEqual(sess2.model_id, "qwen-max")
                self.assertTrue(str(cfg2.model).endswith("qwen-max") or "qwen-max" in str(cfg2.model))


if __name__ == "__main__":
    unittest.main()
