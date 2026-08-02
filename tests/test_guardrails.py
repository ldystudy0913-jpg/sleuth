"""Tests for product disclosure guardrails."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sleuth.config import Config, _apply_env
from sleuth.guardrails import (
    DENY_MESSAGE,
    bash_command_blocked,
    deny_if_protected,
    is_protected_path,
    package_root,
)
from sleuth.prompts import assemble
from sleuth.skill import SkillInfo, set_skills
from sleuth.tools.base import ToolContext
from sleuth.tools.read import ReadTool
from sleuth.permission import Permission, allow_all_rules


class GuardrailPathTests(unittest.TestCase):
    def test_package_file_is_protected(self):
        target = package_root() / "session.py"
        self.assertTrue(target.is_file())
        self.assertTrue(is_protected_path(target))

    def test_user_file_not_protected(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "app.py"
            f.write_text("print(1)\n", encoding="utf-8")
            self.assertFalse(is_protected_path(f, workdir=Path(td)))

    def test_env_secret_protected(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("KEY=1\n", encoding="utf-8")
            self.assertTrue(is_protected_path(env, workdir=Path(td)))
            self.assertIsNotNone(deny_if_protected(env, workdir=Path(td), enabled=True))
            self.assertIsNone(deny_if_protected(env, workdir=Path(td), enabled=False))


class GuardrailToolTests(unittest.TestCase):
    def test_read_blocks_package_source(self):
        ctx = ToolContext(
            workdir=Path.cwd(),
            permission=Permission(rules=allow_all_rules()),
            guardrails_enabled=True,
        )
        result = ReadTool().execute(
            {"file_path": str(package_root() / "session.py")},
            ctx,
        )
        self.assertTrue(result.is_error)
        self.assertIn("guardrails", result.output.lower())

    def test_read_allows_when_disabled(self):
        ctx = ToolContext(
            workdir=Path.cwd(),
            permission=Permission(rules=allow_all_rules()),
            guardrails_enabled=False,
        )
        result = ReadTool().execute(
            {"file_path": str(package_root() / "session.py")},
            ctx,
        )
        self.assertFalse(result.is_error)

    def test_bash_blocks_cat_package(self):
        msg = bash_command_blocked(
            f"type {package_root() / 'session.py'}",
            workdir=Path.cwd(),
            enabled=True,
        )
        self.assertEqual(msg, DENY_MESSAGE)


class GuardrailPromptTests(unittest.TestCase):
    def test_assemble_includes_catalog_when_enabled(self):
        set_skills(
            {
                "demo": SkillInfo(
                    name="demo",
                    description="Demo skill for tests",
                    location=Path("."),
                    content="# demo",
                )
            }
        )
        text = assemble(
            workdir=Path.cwd(),
            config=Config(guardrails=True),
            agent_name="build",
            model="test",
            tool_specs=[{"name": "read", "description": "Read a file"}],
            guardrails=True,
        )
        self.assertIn("Product disclosure policy", text)
        self.assertIn("Public tools", text)
        self.assertIn("`read`", text)
        self.assertIn("Available skills", text)
        self.assertIn("`demo`", text)

    def test_assemble_skips_policy_when_disabled(self):
        text = assemble(
            workdir=Path.cwd(),
            config=Config(guardrails=False),
            agent_name="build",
            model="test",
            tool_specs=[],
            guardrails=False,
        )
        self.assertNotIn("Product disclosure policy", text)


class GuardrailConfigTests(unittest.TestCase):
    def test_env_disables_guardrails(self):
        cfg = Config()
        self.assertTrue(cfg.guardrails)
        with mock.patch.dict(os.environ, {"SLEUTH_GUARDRAILS": "0"}, clear=False):
            _apply_env(cfg)
        self.assertFalse(cfg.guardrails)


if __name__ == "__main__":
    unittest.main()
