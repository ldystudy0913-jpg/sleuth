"""Scaffold generate.py produces a runnable MCP agent package."""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_LEFTOVER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")


def _load_generate():
    import importlib.util

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


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def _tool_params(server, name: str) -> list[str]:
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    return list(inspect.signature(tools[name].fn).parameters)


def _closed_settings(cfg_mod, **kwargs):
    body = {
        "attachments_enabled": False,
        "kb_enabled": False,
        "output_enabled": False,
        "mcp_token": "",
    }
    body.update(kwargs)
    return cfg_mod.Settings(**body)


class _JsonResp:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status
        self.code = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _KbOpener:
    def open(self, req, timeout=None):
        url = getattr(req, "full_url", None) or getattr(req, "get_full_url")()
        if "login" in url or "auth" in url:
            return _JsonResp(
                {
                    "returnCode": "SUC0000",
                    "body": {"ragToken": "tok", "expireTime": 9_999_999_999_999},
                }
            )
        return _JsonResp(
            {
                "returnCode": "SUC0000",
                "body": [
                    {
                        "title": "AML",
                        "fileName": "aml.pdf",
                        "dmzUrl": "https://kb.example/a",
                        "rankScore": 0.9,
                    }
                ],
            }
        )


class AgentScaffoldGenerateTests(unittest.TestCase):
    def test_default_package_local_skill_and_modules(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(name="demo_ops", port=8799, out=Path(td) / "demo_ops")
            _assert_no_leftover(self, dest)
            pkg = dest / "demo_ops"
            self.assertTrue((pkg / "mcp_server.py").is_file())
            self.assertTrue((dest / "skills" / "demo-ops-sop" / "SKILL.md").is_file())
            self.assertTrue((pkg / "attachments.py").is_file())
            self.assertTrue((pkg / "kb.py").is_file())
            self.assertTrue((pkg / "output.py").is_file())
            self.assertFalse((dest / "skills_cos").exists())
            snippet = (dest / "deploy" / "sleuth.env.snippet").read_text(encoding="utf-8")
            self.assertIn('"agent":true', snippet)
            sys.path.insert(0, str(dest))
            try:
                card_mod = __import__("demo_ops.agent_card", fromlist=["*"])
                cfg_mod = __import__("demo_ops.config", fromlist=["*"])
                mcp_mod = __import__("demo_ops.mcp_server", fromlist=["*"])
                card = card_mod.load_agent_card()
                self.assertEqual(card["name"], "demo_ops")
                self.assertEqual(len(card["skills"]), 1)
                self.assertTrue(card["skills"][0].get("content"))
                self.assertEqual(card["skills"][0]["name"], "demo-ops-sop")
                perm = card.get("permission") or {}
                self.assertEqual(perm.get("kb_lookup"), "deny")
                self.assertEqual(perm.get("save_output_file"), "deny")
                self.assertNotIn("demoops_kb_search", perm)
                self.assertNotIn("demoops_emit_file", perm)
                from demo_ops.pipeline import ping as run_ping

                echo = run_ping("hello")
                self.assertEqual(echo.get("echo"), "hello")
                settings = _closed_settings(cfg_mod)
                self.assertFalse(settings.kb_enabled)
                self.assertFalse(settings.output_enabled)
                self.assertFalse(settings.attachments_enabled)
                if _mcp_available():
                    server = mcp_mod.build_mcp_server(settings)
                    tools = {t.name: t for t in server._tool_manager.list_tools()}
                    self.assertIn("ping", tools)
                    self.assertIn("get_agent_card", tools)
                    self.assertIn("health", tools)
                    self.assertNotIn("kb_search", tools)
                    self.assertNotIn("emit_file", tools)
                    self.assertEqual(_tool_params(server, "ping"), ["message"])
            finally:
                _unload("demo_ops", str(dest))

    def test_catalog_skills_name_only_and_local_wins(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(name="demo_ops", out=Path(td) / "demo_ops")
            md = (dest / "agent.md").read_text(encoding="utf-8")
            md = md.replace(
                "mode: primary",
                "mode: primary\ncatalog_skills:\n  - kyc-shared",
            )
            (dest / "agent.md").write_text(md, encoding="utf-8")
            sys.path.insert(0, str(dest))
            try:
                card_mod = __import__("demo_ops.agent_card", fromlist=["*"])
                card = card_mod.load_agent_card()
                names = [s["name"] for s in card["skills"]]
                self.assertEqual(names, ["demo-ops-sop", "kyc-shared"])
                shared = next(s for s in card["skills"] if s["name"] == "kyc-shared")
                self.assertNotIn("content", shared)

                skill_dir = dest / "skills" / "kyc-shared"
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(
                    "---\nname: kyc-shared\ndescription: local\n---\n\n# local sop\n",
                    encoding="utf-8",
                )
                card2 = card_mod.load_agent_card()
                shared2 = next(s for s in card2["skills"] if s["name"] == "kyc-shared")
                self.assertIn("local sop", shared2.get("content") or "")
                self.assertEqual(
                    [s["name"] for s in card2["skills"]].count("kyc-shared"), 1
                )
            finally:
                _unload("demo_ops", str(dest))

    def test_tools_only_sets_agent_false(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(
                name="demo_ops",
                out=Path(td) / "demo_ops",
                tools_only=True,
            )
            snippet = (dest / "deploy" / "sleuth.env.snippet").read_text(encoding="utf-8")
            self.assertIn('"agent":false', snippet)
            self.assertNotIn('"agent":true', snippet)

    def test_attachments_env_declares_ping_refs(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(name="demo_ops", out=Path(td) / "demo_ops")
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
                if _mcp_available():
                    cfg_mod = __import__("demo_ops.config", fromlist=["*"])
                    mcp_mod = __import__("demo_ops.mcp_server", fromlist=["*"])
                    server = mcp_mod.build_mcp_server(
                        _closed_settings(cfg_mod, attachments_enabled=True)
                    )
                    self.assertEqual(
                        _tool_params(server, "ping"),
                        ["message", "attachment_refs_json"],
                    )
            finally:
                _unload("demo_ops", str(dest))

    def test_kb_registers_only_with_pkg_env(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(name="demo_ops", out=Path(td) / "demo_ops")
            sys.path.insert(0, str(dest))
            try:
                cfg_mod = __import__("demo_ops.config", fromlist=["*"])
                card_mod = __import__("demo_ops.agent_card", fromlist=["*"])
                kb_mod = __import__("demo_ops.kb", fromlist=["*"])
                kb_mod.reset_token_cache()
                sleuth_only = {
                    "SLEUTH_KB_API_URL": "http://kb.example/search",
                    "SLEUTH_KB_LOGIN_URL": "http://kb.example/login",
                    "SLEUTH_KB_OPENID": "oid",
                    "SLEUTH_KB_SERVICEID": "sid",
                }
                with patch.object(cfg_mod, "_load_dotenv"):
                    with patch.dict(os.environ, sleuth_only, clear=False):
                        for k in list(os.environ):
                            if k.startswith("DEMO_OPS_"):
                                os.environ.pop(k, None)
                        idle = cfg_mod.Settings()
                        self.assertFalse(idle.kb_enabled)
                kb_settings = cfg_mod.Settings(
                    kb_api_url="http://kb.example/search",
                    kb_login_url="http://kb.example/login",
                    kb_openid="oid",
                    kb_service_id="sid",
                )
                self.assertTrue(kb_settings.kb_enabled)
                body = kb_mod.search("what is aml", kb_settings, opener=_KbOpener())
                self.assertTrue(body.get("ok"))
                self.assertTrue(body.get("sources"))
                self.assertEqual(body["sources"][0]["url"], "https://kb.example/a")
                card = card_mod.load_agent_card(settings=kb_settings)
                perm = card.get("permission") or {}
                self.assertEqual(perm.get("kb_lookup"), "deny")
                self.assertEqual(perm.get("demoops_kb_search"), "allow")
                if _mcp_available():
                    mcp_mod = __import__("demo_ops.mcp_server", fromlist=["*"])
                    tools = {
                        t.name: t
                        for t in mcp_mod.build_mcp_server(kb_settings)._tool_manager.list_tools()
                    }
                    self.assertIn("kb_search", tools)
                    idle_tools = {
                        t.name: t
                        for t in mcp_mod.build_mcp_server(
                            _closed_settings(cfg_mod)
                        )._tool_manager.list_tools()
                    }
                    self.assertNotIn("kb_search", idle_tools)
            finally:
                _unload("demo_ops", str(dest))

    def test_output_registers_with_cos_env(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(name="demo_ops", out=Path(td) / "demo_ops")
            sys.path.insert(0, str(dest))
            try:
                cfg_mod = __import__("demo_ops.config", fromlist=["*"])
                card_mod = __import__("demo_ops.agent_card", fromlist=["*"])
                from demo_ops.output import emit_file

                cos = cfg_mod.Settings(
                    cos_secret_id="id",
                    cos_secret_key="secret",
                    cos_bucket="bucket",
                    cos_region="ap-southeast-1",
                )
                self.assertTrue(cos.output_enabled)
                card = card_mod.load_agent_card(settings=cos)
                perm = card.get("permission") or {}
                self.assertEqual(perm.get("save_output_file"), "deny")
                self.assertEqual(perm.get("demoops_emit_file"), "allow")
                body = emit_file(
                    cos,
                    filename="note.txt",
                    url="https://example.com/note.txt",
                    mime="text/plain",
                )
                self.assertTrue(body.get("ok"))
                self.assertEqual(body["files"][0]["filename"], "note.txt")
                blocked = emit_file(cos, filename="x", url="data:text/plain,hi")
                self.assertFalse(blocked.get("ok"))
                self.assertEqual(blocked.get("files"), [])
                if _mcp_available():
                    mcp_mod = __import__("demo_ops.mcp_server", fromlist=["*"])
                    tools = {
                        t.name: t
                        for t in mcp_mod.build_mcp_server(cos)._tool_manager.list_tools()
                    }
                    self.assertIn("emit_file", tools)
            finally:
                _unload("demo_ops", str(dest))

    def test_empty_mcp_token_skips_middleware(self):
        with tempfile.TemporaryDirectory() as td:
            dest = generate(name="demo_ops", out=Path(td) / "demo_ops")
            sys.path.insert(0, str(dest))
            try:
                from demo_ops.mcp_server import mcp_token_ok

                self.assertTrue(mcp_token_ok("/health", "", "secret"))
                self.assertTrue(mcp_token_ok("/mcp", "", ""))
                self.assertFalse(mcp_token_ok("/mcp", "", "secret"))
                self.assertTrue(mcp_token_ok("/mcp", "Bearer secret", "secret"))
                if not _mcp_available():
                    return
                cfg_mod = __import__("demo_ops.config", fromlist=["*"])
                mcp_mod = __import__("demo_ops.mcp_server", fromlist=["*"])
                open_srv = mcp_mod.build_mcp_server(_closed_settings(cfg_mod))
                auth_srv = mcp_mod.build_mcp_server(
                    _closed_settings(cfg_mod, mcp_token="secret")
                )
                self.assertEqual(open_srv.streamable_http_app.__name__, "streamable_http_app")
                self.assertEqual(auth_srv.streamable_http_app.__name__, "wrapped")
            finally:
                _unload("demo_ops", str(dest))


if __name__ == "__main__":
    unittest.main()
