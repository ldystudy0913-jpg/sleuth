"""Scaffold generate.py produces a runnable MCP agent package."""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import re
import sys
import tempfile
import unittest
from pathlib import Path

_LEFTOVER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")


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


def _unload(prefix: str, dest: str) -> None:
    for key in list(sys.modules):
        if key == prefix or key.startswith(prefix + "."):
            sys.modules.pop(key, None)
    if sys.path and sys.path[0] == dest:
        sys.path.pop(0)


def _assert_no_leftover(test: unittest.TestCase, dest: Path) -> None:
    test.assertEqual(leftover_placeholders(dest), [])
    hits = []
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _LEFTOVER_RE.search(text):
            hits.append(str(path.relative_to(dest)))
    test.assertEqual(hits, [])


def _ping_args(src: str) -> list[str]:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "build_mcp_server":
            for inner in node.body:
                if isinstance(inner, ast.FunctionDef) and inner.name == "ping":
                    return [a.arg for a in inner.args.args]
    raise AssertionError("ping not found in build_mcp_server")


class AgentScaffoldGenerateTests(unittest.TestCase):
    def test_private_package_imports_and_card_embeds_skill(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                port=8799,
                skill="private",
                out=Path(td) / "demo_ops",
            )
            _assert_no_leftover(self, dest)
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
                _unload("demo_ops", str(dest))

    def test_default_omits_optional_modules(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="private",
                out=Path(td) / "demo_ops",
            )
            pkg = dest / "demo_ops"
            self.assertFalse((pkg / "kb.py").is_file())
            self.assertFalse((pkg / "attachments.py").is_file())
            self.assertFalse((pkg / "output.py").is_file())
            mcp_src = (pkg / "mcp_server.py").read_text(encoding="utf-8")
            self.assertNotIn("attachment_refs_json", mcp_src)
            self.assertEqual(_ping_args(mcp_src), ["message"])
            sys.path.insert(0, str(dest))
            try:
                card_mod = importlib.import_module("demo_ops.agent_card")
                card = card_mod.load_agent_card()
                perm = card.get("permission") or {}
                self.assertEqual(perm.get("kb_lookup"), "deny")
                self.assertEqual(perm.get("save_output_file"), "deny")
                self.assertNotIn("demoops_kb_search", perm)
                self.assertNotIn("demoops_emit_file", perm)
                from demo_ops.pipeline import ping as run_ping

                self.assertNotIn("attachment_refs", inspect.signature(run_ping).parameters)
            finally:
                _unload("demo_ops", str(dest))

    def test_attachments_helper_and_ping_schema(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="private",
                out=Path(td) / "demo_ops",
                attachments=True,
            )
            _assert_no_leftover(self, dest)
            pkg = dest / "demo_ops"
            self.assertTrue((pkg / "attachments.py").is_file())
            mcp_src = (pkg / "mcp_server.py").read_text(encoding="utf-8")
            self.assertIn("attachment_refs_json", mcp_src)
            self.assertEqual(_ping_args(mcp_src), ["message", "attachment_refs_json"])
            sys.path.insert(0, str(dest))
            try:
                from demo_ops.attachments import load_excerpts, summarize_refs
                from demo_ops.pipeline import ping as run_ping

                excerpts, skipped = load_excerpts(
                    [{"filename": "a.pdf", "excerpt": "hello excerpt"}]
                )
                self.assertEqual(excerpts, ["hello excerpt"])
                self.assertEqual(skipped, [])
                _, skipped_enc = load_excerpts(
                    [{"filename": "secret.bin", "encrypted": True}]
                )
                self.assertTrue(any("encrypted" in s for s in skipped_enc))
                _, skipped_data = load_excerpts(
                    [{"filename": "x", "url": "data:text/plain,hi"}]
                )
                self.assertTrue(any("data/file" in s for s in skipped_data))
                echoed = run_ping(
                    "hi",
                    attachment_refs=[{"filename": "a.pdf", "excerpt": "body"}],
                )
                self.assertEqual(echoed.get("excerpt_count"), 1)
                self.assertIn("body", echoed.get("excerpts") or [])
                self.assertIn("attachment_refs", inspect.signature(run_ping).parameters)
                summary = summarize_refs([])
                self.assertEqual(summary.get("attachment_count"), 0)
            finally:
                _unload("demo_ops", str(dest))

    def test_kb_search_and_card_denies_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="private",
                out=Path(td) / "demo_ops",
                kb=True,
            )
            _assert_no_leftover(self, dest)
            self.assertTrue((dest / "demo_ops" / "kb.py").is_file())
            sys.path.insert(0, str(dest))
            try:
                card_mod = importlib.import_module("demo_ops.agent_card")
                cfg_mod = importlib.import_module("demo_ops.config")
                from demo_ops.kb import search

                card = card_mod.load_agent_card()
                perm = card.get("permission") or {}
                self.assertEqual(perm.get("kb_lookup"), "deny")
                self.assertEqual(perm.get("demoops_kb_search"), "allow")
                body = search("what is aml", cfg_mod.Settings())
                self.assertEqual(body.get("sources"), [])
                self.assertFalse(body.get("ok"))
                try:
                    import mcp  # noqa: F401
                except ImportError:
                    pass
                else:
                    mcp_mod = importlib.import_module("demo_ops.mcp_server")
                    server = mcp_mod.build_mcp_server(cfg_mod.Settings())
                    tools = {t.name: t for t in server._tool_manager.list_tools()}
                    self.assertIn("kb_search", tools)
            finally:
                _unload("demo_ops", str(dest))

    def test_output_emit_file_json_skeleton(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                skill="private",
                out=Path(td) / "demo_ops",
                output=True,
            )
            _assert_no_leftover(self, dest)
            self.assertTrue((dest / "demo_ops" / "output.py").is_file())
            sys.path.insert(0, str(dest))
            try:
                card_mod = importlib.import_module("demo_ops.agent_card")
                from demo_ops.output import emit_file

                card = card_mod.load_agent_card()
                perm = card.get("permission") or {}
                self.assertEqual(perm.get("save_output_file"), "allow")
                self.assertEqual(perm.get("demoops_emit_file"), "allow")
                body = emit_file(
                    filename="note.txt",
                    url="https://example.com/note.txt",
                    mime="text/plain",
                )
                self.assertTrue(body.get("ok"))
                self.assertIn("files", body)
                self.assertEqual(body["files"][0]["filename"], "note.txt")
                self.assertEqual(body["files"][0]["url"], "https://example.com/note.txt")
                blocked = emit_file(filename="x", url="data:text/plain,hi")
                self.assertFalse(blocked.get("ok"))
                self.assertEqual(blocked.get("files"), [])
                try:
                    import mcp  # noqa: F401
                except ImportError:
                    pass
                else:
                    mcp_mod = importlib.import_module("demo_ops.mcp_server")
                    cfg_mod = importlib.import_module("demo_ops.config")
                    server = mcp_mod.build_mcp_server(cfg_mod.Settings())
                    tools = {t.name: t for t in server._tool_manager.list_tools()}
                    self.assertIn("emit_file", tools)
            finally:
                _unload("demo_ops", str(dest))

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
                _unload("demo_ops", str(dest))

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
                _unload("demo_ops", str(dest))


if __name__ == "__main__":
    unittest.main()
