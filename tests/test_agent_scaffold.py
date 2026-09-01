"""Scaffold generate.py produces a runnable MCP agent package."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def _load_generate():
    path = Path(__file__).resolve().parents[1] / "agents" / "scaffold" / "generate.py"
    spec = importlib.util.spec_from_file_location("sleuth_agent_scaffold_generate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_gen = _load_generate()
generate = _gen.generate
leftover_placeholders = _gen.leftover_placeholders


class AgentScaffoldGenerateTests(unittest.TestCase):
    def test_private_package_imports_and_card_embeds_skill(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                port=8799,
                skill="private",
                out=Path(td) / "demo_ops",
            )
            self.assertEqual(leftover_placeholders(dest), [])
            self.assertTrue((dest / "demo_ops" / "mcp_server.py").is_file())
            self.assertTrue((dest / "skills" / "demo-ops-sop" / "SKILL.md").is_file())
            sys.path.insert(0, str(dest))
            try:
                card_mod = importlib.import_module("demo_ops.agent_card")
                mcp_mod = importlib.import_module("demo_ops.mcp_server")
                cfg_mod = importlib.import_module("demo_ops.config")
                card = card_mod.load_agent_card()
                self.assertEqual(card["name"], "demo_ops")
                self.assertEqual(card_mod.SKILL_MODE, "private")
                self.assertEqual(len(card["skills"]), 1)
                self.assertTrue(card["skills"][0].get("content"))
                self.assertEqual(card["skills"][0]["name"], "demo-ops-sop")
                from demo_ops.pipeline import ping as run_ping

                echo = run_ping("hello")
                self.assertEqual(echo.get("echo"), "hello")
                try:
                    import mcp  # noqa: F401
                except ImportError:
                    pass
                else:
                    server = mcp_mod.build_mcp_server(cfg_mod.Settings())
                    tools = {t.name: t for t in server._tool_manager.list_tools()}
                    self.assertIn("ping", tools)
                    self.assertIn("get_agent_card", tools)
                    self.assertIn("health", tools)
            finally:
                for key in list(sys.modules):
                    if key == "demo_ops" or key.startswith("demo_ops."):
                        sys.modules.pop(key, None)
                if sys.path and sys.path[0] == str(dest):
                    sys.path.pop(0)

    def test_cos_card_has_name_only(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="cos",
                out=Path(td) / "demo_ops",
            )
            sys.path.insert(0, str(dest))
            try:
                card_mod = importlib.import_module("demo_ops.agent_card")
                card = card_mod.load_agent_card()
                self.assertEqual(card_mod.SKILL_MODE, "cos")
                self.assertEqual(len(card["skills"]), 1)
                self.assertEqual(card["skills"][0]["name"], "demo-ops-shared")
                self.assertNotIn("content", card["skills"][0])
            finally:
                for key in list(sys.modules):
                    if key == "demo_ops" or key.startswith("demo_ops."):
                        sys.modules.pop(key, None)
                if sys.path and sys.path[0] == str(dest):
                    sys.path.pop(0)

    def test_none_sets_agent_false_in_snippet(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="none",
                out=Path(td) / "demo_ops",
            )
            snippet = (dest / "deploy" / "sleuth.env.snippet").read_text(encoding="utf-8")
            self.assertIn('"agent":false', snippet)
            self.assertNotIn('"agent":true', snippet)

    def test_both_lists_private_and_cos(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="both",
                out=Path(td) / "demo_ops",
            )
            sys.path.insert(0, str(dest))
            try:
                card_mod = importlib.import_module("demo_ops.agent_card")
                card = card_mod.load_agent_card()
                names = [s["name"] for s in card["skills"]]
                self.assertEqual(names, ["demo-ops-sop", "demo-ops-shared"])
                self.assertTrue(card["skills"][0].get("content"))
                self.assertNotIn("content", card["skills"][1])
            finally:
                for key in list(sys.modules):
                    if key == "demo_ops" or key.startswith("demo_ops."):
                        sys.modules.pop(key, None)
                if sys.path and sys.path[0] == str(dest):
                    sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
