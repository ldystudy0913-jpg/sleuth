"""Unit tests for skills materialize, env config, and sqlite store."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from sleuth.config import Config, SkillsConfig, SkillS3Entry, _apply_env
from sleuth.skill import _materialize_bytes, _collect_from_root, discover_skills
from sleuth.storage.sqlite import SQLiteStore
from sleuth.storage.base import SessionRecord, UsageEvent
from sleuth.messages import Message


class SkillMaterializeTests(unittest.TestCase):
    def test_zip_with_multiple_skills(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "alpha/SKILL.md",
                "---\nname: alpha\ndescription: A\n---\n\n# Alpha\n",
            )
            zf.writestr(
                "beta/SKILL.md",
                "---\nname: beta\ndescription: B\n---\n\n# Beta\n",
            )
        root = _materialize_bytes(buf.getvalue(), "test:multi-zip")
        self.assertIsNotNone(root)
        found = {}
        _collect_from_root(root, found)
        self.assertEqual(set(found), {"alpha", "beta"})

    def test_discover_local_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill_dir = root / "myskill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: localpack\ndescription: from path\n---\n\nbody\n",
                encoding="utf-8",
            )
            cfg = Config(skills=SkillsConfig(paths=[str(root)]))
            found = discover_skills(cfg, cwd=root)
            self.assertIn("localpack", found)


class EnvConfigTests(unittest.TestCase):
    def test_apply_env_storage_and_skills(self):
        old = dict(os.environ)
        try:
            os.environ["SLEUTH_MODEL"] = "openai/test-model"
            os.environ["SLEUTH_STORAGE_BACKEND"] = "sqlite"
            os.environ["SLEUTH_USER_ID"] = "u42"
            os.environ["SLEUTH_SKILLS_REFRESH_SECONDS"] = "120"
            os.environ["SLEUTH_SKILLS_URLS"] = (
                "https://example.com/a.zip,https://example.com/b.zip"
            )
            os.environ["SLEUTH_SKILLS_S3"] = json.dumps(
                [{"uri": "s3://bucket/pack.zip"}, {"bucket": "b", "prefix": "skills/"}]
            )
            cfg = Config()
            _apply_env(cfg)
            self.assertEqual(cfg.model, "openai/test-model")
            self.assertEqual(cfg.user_id, "u42")
            self.assertEqual(cfg.skills.refresh_seconds, 120)
            self.assertEqual(len(cfg.skills.urls), 2)
            self.assertEqual(len(cfg.skills.s3), 2)
            self.assertTrue(cfg.skills.s3[1].prefix)
        finally:
            os.environ.clear()
            os.environ.update(old)


class SqliteStoreTests(unittest.TestCase):
    def test_user_session_usage_todos(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db = Path(td) / "t.db"
            store = SQLiteStore(db)
            rec = SessionRecord(
                id="sess_test1",
                directory="/tmp",
                title="t",
                user_id="alice",
            )
            store.create_session(rec)
            msg = Message.user_text("hello")
            mid = store.save_message(rec.id, msg)
            self.assertTrue(mid)
            store.save_usage_event(
                UsageEvent(
                    user_id="alice",
                    session_id=rec.id,
                    message_id=mid,
                    model="m",
                    tokens_input=10,
                    tokens_output=5,
                    cost=0.01,
                )
            )
            store.save_todos(
                rec.id, [{"content": "x", "status": "pending", "priority": "normal"}]
            )
            listed = store.list_sessions(user_id="alice")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].user_id, "alice")
            usage = store.sum_usage("alice")
            self.assertEqual(usage["tokens_input"], 10)
            todos = store.load_todos(rec.id)
            self.assertEqual(todos[0]["content"], "x")

            store.replace_messages(rec.id, [Message.user_text("compacted")])
            msgs = store.load_messages(rec.id)
            self.assertEqual(len(msgs), 1)
            self.assertIn("compacted", msgs[0].text)


class AppImportTests(unittest.TestCase):
    def test_core_imports(self):
        from sleuth.app import build_session, build_registry, reload_skills
        from sleuth.storage.factory import create_store
        from sleuth.server.app import create_app
        from sleuth.tools.registry import ToolRegistry

        self.assertTrue(callable(build_session))
        self.assertTrue(callable(build_registry))
        self.assertTrue(callable(reload_skills))
        self.assertTrue(callable(create_store))
        self.assertTrue(callable(create_app))
        names = ToolRegistry().names()
        self.assertIn("skill", names)
        self.assertIn("read", names)


if __name__ == "__main__":
    unittest.main()
