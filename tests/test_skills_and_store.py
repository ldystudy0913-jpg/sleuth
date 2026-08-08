"""Unit tests for skills materialize, env config, and sqlite store."""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sleuth.config import Config, SkillsConfig, _apply_env
from sleuth.skill import (
    _materialize_bytes,
    _collect_from_root,
    _cache_dir,
    _safe_slug,
    discover_skills,
    ensure_skills_fresh,
    get_skills,
    set_skills,
    refresh_skills,
)
import sleuth.skill as skill_mod
from sleuth.storage.sqlite import SQLiteStore
from sleuth.storage.base import SessionRecord, UsageEvent
from sleuth.messages import Message


class SkillMaterializeTests(unittest.TestCase):
    def test_zip_with_multiple_skills(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            old = os.environ.get("SLEUTH_DATA_DIR")
            os.environ["SLEUTH_DATA_DIR"] = td
            try:
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
            finally:
                if old is None:
                    os.environ.pop("SLEUTH_DATA_DIR", None)
                else:
                    os.environ["SLEUTH_DATA_DIR"] = old

    def test_materialize_replace_keeps_complete_tree(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            old = os.environ.get("SLEUTH_DATA_DIR")
            os.environ["SLEUTH_DATA_DIR"] = td
            try:
                key = "test:replace-zip"
                buf1 = io.BytesIO()
                with zipfile.ZipFile(buf1, "w") as zf:
                    zf.writestr(
                        "v1/SKILL.md",
                        "---\nname: pack\ndescription: v1\n---\n\n# v1\n",
                    )
                root1 = _materialize_bytes(buf1.getvalue(), key)
                self.assertIsNotNone(root1)
                self.assertTrue((root1 / "v1" / "SKILL.md").is_file())

                buf2 = io.BytesIO()
                with zipfile.ZipFile(buf2, "w") as zf:
                    zf.writestr(
                        "v2/SKILL.md",
                        "---\nname: pack\ndescription: v2\n---\n\n# v2\n",
                    )
                root2 = _materialize_bytes(buf2.getvalue(), key)
                self.assertIsNotNone(root2)
                self.assertEqual(root1, root2)
                self.assertTrue((root2 / "v2" / "SKILL.md").is_file())
                self.assertFalse((root2 / "v1" / "SKILL.md").exists())
                slug = _safe_slug(key)
                cache = _cache_dir()
                leftovers = [
                    p for p in cache.iterdir()
                    if p.name.startswith(f".{slug}.tmp-") or p.name.startswith(f".{slug}.old-")
                ]
                self.assertEqual(leftovers, [])
            finally:
                if old is None:
                    os.environ.pop("SLEUTH_DATA_DIR", None)
                else:
                    os.environ["SLEUTH_DATA_DIR"] = old

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


class SkillRefreshTests(unittest.TestCase):
    def tearDown(self):
        set_skills({})
        skill_mod._LAST_REFRESH = 0.0
        with skill_mod._REFRESH_GATE:
            skill_mod._REFRESH_EVENT = None

    def test_ensure_fresh_skips_within_ttl_then_picks_up_new_skill(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            first = root / "one"
            first.mkdir()
            (first / "SKILL.md").write_text(
                "---\nname: one\ndescription: first\n---\n\n# one\n",
                encoding="utf-8",
            )
            cfg = Config(skills=SkillsConfig(paths=[str(root)], refresh_seconds=300))
            found = refresh_skills(cfg, root, force=True)
            self.assertIn("one", found)
            self.assertNotIn("two", get_skills())

            # Still within TTL: new skill on disk must not appear yet.
            second = root / "two"
            second.mkdir()
            (second / "SKILL.md").write_text(
                "---\nname: two\ndescription: second\n---\n\n# two\n",
                encoding="utf-8",
            )
            skill_mod._LAST_REFRESH = skill_mod.time.time()
            mid = ensure_skills_fresh(cfg, root)
            self.assertIn("one", mid)
            self.assertNotIn("two", mid)

            # Past TTL: rediscover picks up the new skill.
            skill_mod._LAST_REFRESH = skill_mod.time.time() - 400
            later = ensure_skills_fresh(cfg, root)
            self.assertIn("one", later)
            self.assertIn("two", later)

    def test_ensure_fresh_single_flight_one_discover(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            skill_dir = root / "one"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: one\ndescription: first\n---\n\n# one\n",
                encoding="utf-8",
            )
            cfg = Config(skills=SkillsConfig(paths=[str(root)], refresh_seconds=60))
            refresh_skills(cfg, root, force=True)
            skill_mod._LAST_REFRESH = skill_mod.time.time() - 120

            results: list = []
            errors: list = []
            call_count = {"n": 0}
            entered = threading.Event()
            release = threading.Event()
            real_discover = skill_mod.discover_skills

            def counting_discover(*args, **kwargs):
                call_count["n"] += 1
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                return real_discover(*args, **kwargs)

            def worker():
                try:
                    results.append(ensure_skills_fresh(cfg, root))
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            with patch.object(skill_mod, "discover_skills", side_effect=counting_discover):
                t1 = threading.Thread(target=worker)
                t2 = threading.Thread(target=worker)
                t1.start()
                t2.start()
                self.assertTrue(entered.wait(timeout=5))
                # Let the second thread become a waiter on the in-flight refresh.
                skill_mod.time.sleep(0.15)
                release.set()
                t1.join(timeout=10)
                t2.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(call_count["n"], 1)
            self.assertEqual(len(results), 2)
            for catalog in results:
                self.assertIn("one", catalog)


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
