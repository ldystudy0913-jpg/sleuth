"""Tests for MCP tool bridging: schema exposure and argument passthrough."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock, patch

from sleuth.mcp.bridge import McpBridgeTool, bridge_tools
from sleuth.mcp.manager import McpToolInfo
from sleuth.permission import Permission, Rule
from sleuth.tools.base import ToolContext, to_provider_spec, validate_args
from sleuth.tools.registry import ToolRegistry


def _info(
    *,
    server: str = "anti_money_laundry",
    name: str = "search_tables",
    qualified: str = "anti_money_laundry_search_tables",
    schema: Dict[str, Any] | None = None,
) -> McpToolInfo:
    return McpToolInfo(
        server=server,
        name=name,
        qualified=qualified,
        description="Search tables by keyword",
        input_schema=schema
        or {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "table keyword"},
                "limit": {"type": "integer", "description": "max rows"},
            },
            "required": ["keyword"],
        },
    )


class McpBridgeSchemaTests(unittest.TestCase):
    def test_provider_spec_uses_original_mcp_schema(self):
        tool = McpBridgeTool(_info(), MagicMock())
        spec = to_provider_spec(tool)
        props = spec["parameters_json_schema"]["properties"]
        self.assertEqual(props["keyword"]["type"], "string")
        self.assertEqual(props["limit"]["type"], "integer")
        self.assertEqual(spec["parameters_json_schema"]["required"], ["keyword"])

    def test_reserved_property_name_schema_does_not_warn(self):
        """MCP params named ``schema`` must not create a Pydantic field."""
        import warnings

        schema = {
            "type": "object",
            "properties": {
                "schema": {"type": "string"},
                "layer": {"type": "string"},
            },
            "required": ["schema"],
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tool = McpBridgeTool(
                _info(
                    server="database",
                    name="list_tables_by_layer",
                    qualified="database_list_tables_by_layer",
                    schema=schema,
                ),
                MagicMock(),
            )
            to_provider_spec(tool)
            validate_args(tool, {"schema": "public", "layer": "ods"})
        shadow = [
            w
            for w in caught
            if "shadows an attribute" in str(w.message)
            or "Field name \"schema\"" in str(w.message)
        ]
        self.assertEqual(shadow, [])
        spec = to_provider_spec(tool)
        self.assertEqual(
            spec["parameters_json_schema"]["properties"]["schema"]["type"],
            "string",
        )

    def test_validate_args_passthrough_keeps_model_values(self):
        tool = McpBridgeTool(_info(), MagicMock())
        raw = {"keyword": "I06_CDK_INF_S", "limit": 50}
        parsed, err = validate_args(tool, raw)
        self.assertIsNone(err)
        self.assertEqual(parsed, raw)

    def test_validate_args_does_not_inject_null_optionals(self):
        tool = McpBridgeTool(_info(), MagicMock())
        raw = {"keyword": "I06_CDK_INF_S"}
        parsed, err = validate_args(tool, raw)
        self.assertIsNone(err)
        self.assertEqual(parsed, {"keyword": "I06_CDK_INF_S"})
        self.assertNotIn("limit", parsed)

    def test_empty_schema_still_forwards_raw_args(self):
        tool = McpBridgeTool(
            _info(schema={"type": "object", "properties": {}}),
            MagicMock(),
        )
        raw = {"keyword": "x", "limit": 10}
        parsed, err = validate_args(tool, raw)
        self.assertIsNone(err)
        self.assertEqual(parsed, raw)


class McpBridgeExecuteTests(unittest.TestCase):
    def test_registry_forwards_unmangled_args_to_manager(self):
        calls: list[Tuple[str, Dict[str, Any]]] = []

        class FakeManager:
            def call_tool(
                self,
                qualified_name: str,
                arguments: Dict[str, Any],
                progress_callback=None,
            ):
                calls.append((qualified_name, dict(arguments)))
                return ("ok", False)

        tool = McpBridgeTool(_info(), FakeManager())  # type: ignore[arg-type]
        registry = ToolRegistry(tools=[])
        registry.register(tool)

        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
        )
        result = registry.execute(
            "anti_money_laundry_search_tables",
            {"keyword": "I06_CDK_INF_S", "limit": 50},
            ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "anti_money_laundry_search_tables")
        self.assertEqual(
            calls[0][1],
            {"keyword": "I06_CDK_INF_S", "limit": 50},
        )

    def test_bridge_tools_wraps_manager_catalog(self):
        manager = MagicMock()
        manager.tools = {
            "anti_money_laundry_search_tables": _info(),
        }
        tools = bridge_tools(manager)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "anti_money_laundry_search_tables")
        self.assertTrue(tools[0].skip_strict_validation)


class McpBridgeMailboxTests(unittest.TestCase):
    def test_injects_attachment_refs_json_and_harvests_files(self):
        from sleuth.config import Config
        from sleuth.files.cos import MemoryObjectStore
        from sleuth.files.mailbox import write_session_files

        cfg = Config()
        cfg.cos.secret_id = "id"
        cfg.cos.secret_key = "key"
        cfg.cos.bucket = "b"
        cfg.cos.region = "ap-guangzhou"
        mem = MemoryObjectStore()
        mem.put_bytes(key="k/a.txt", data=b"hi", mime="text/plain")

        class Sess:
            id = "sess_bridge"
            user_id = "alice"
            config = cfg
            store = None
            _files = []
            _prompt_file_ids = None
            _turn_file_ids = []

        sess = Sess()
        write_session_files(
            sess,
            [
                {
                    "id": "file_aaa",
                    "role": "user",
                    "filename": "a.txt",
                    "mime": "text/plain",
                    "size": 2,
                    "object_key": "k/a.txt",
                    "status": "ready",
                }
            ],
        )
        calls: list = []

        class FakeManager:
            def call_tool(
                self,
                qualified_name: str,
                arguments: dict,
                progress_callback=None,
            ):
                calls.append((qualified_name, dict(arguments)))
                return (
                    json.dumps(
                        {
                            "markdown": "ok",
                            "files": [
                                {
                                    "filename": "out.txt",
                                    "mime": "text/plain",
                                    "object_key": "k/out.txt",
                                    "size": 2,
                                    "url": "https://cos.example/out.txt",
                                }
                            ],
                            "sources": [
                                {
                                    "title": "手册.pdf",
                                    "url": "https://kb.example/manual.pdf",
                                }
                            ],
                        }
                    ),
                    False,
                )

        schema = {
            "type": "object",
            "properties": {
                "risk_codes_json": {"type": "string"},
                "attachment_refs_json": {"type": "string"},
            },
        }
        tool = McpBridgeTool(
            _info(
                server="ddreply",
                name="generate_reply_framework",
                qualified="ddreply_generate_reply_framework",
                schema=schema,
            ),
            FakeManager(),  # type: ignore[arg-type]
        )
        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=sess,
        )
        with patch(
            "sleuth.files.mailbox.object_store_from_config", return_value=mem
        ):
            result = tool.execute({"risk_codes_json": '["C001"]'}, ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(len(calls), 1)
        injected = json.loads(calls[0][1]["attachment_refs_json"])
        self.assertEqual(injected[0]["file_id"], "file_aaa")
        self.assertTrue(str(injected[0]["url"]).startswith("/v1/sessions/"))
        self.assertNotIn("local_paths_json", calls[0][1])
        self.assertTrue(result.attachments)
        self.assertEqual(result.attachments[0]["filename"], "out.txt")
        self.assertTrue(sess._turn_file_ids)
        self.assertEqual(
            result.metadata.get("sources"),
            [{"title": "手册.pdf", "url": "https://kb.example/manual.pdf"}],
        )

    def test_injects_sleuth_llm_json_without_mutating_caller_args(self):
        from sleuth.config import Config

        calls: list = []

        class FakeManager:
            def call_tool(
                self,
                qualified_name: str,
                arguments: dict,
                progress_callback=None,
            ):
                calls.append(dict(arguments))
                return ("{}", False)

        class Prov:
            api_key = "sk-sess"
            base_url = "https://llm.example/v1"

        class Sess:
            id = "sess_llm"
            user_id = "alice"
            config = Config()
            provider = Prov()
            model_id = "picked-model"
            store = None
            _files = []
            _turn_file_ids = []

        schema = {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "sleuth_llm_json": {"type": "string"},
            },
        }
        tool = McpBridgeTool(
            _info(
                server="ddcheck",
                name="check_report",
                qualified="ddcheck_check_report",
                schema=schema,
            ),
            FakeManager(),  # type: ignore[arg-type]
        )
        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        raw = {"question": "check", "sleuth_llm_json": "ignore-me"}
        result = tool.execute(raw, ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(raw["sleuth_llm_json"], "ignore-me")
        injected = json.loads(calls[0]["sleuth_llm_json"])
        self.assertEqual(injected["model"], "picked-model")
        self.assertEqual(injected["api_key"], "sk-sess")
        self.assertEqual(injected["base_url"], "https://llm.example/v1")

    def test_skips_sleuth_llm_json_when_schema_omits_it(self):
        from sleuth.config import Config

        calls: list = []

        class FakeManager:
            def call_tool(
                self,
                qualified_name: str,
                arguments: dict,
                progress_callback=None,
            ):
                calls.append(dict(arguments))
                return ("{}", False)

        class Sess:
            id = "sess_llm2"
            provider = type("P", (), {"api_key": "sk", "base_url": "https://x/v1"})()
            model_id = "m"
            config = Config()

        tool = McpBridgeTool(_info(), FakeManager())  # type: ignore[arg-type]
        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        tool.execute({"keyword": "x"}, ctx)
        self.assertNotIn("sleuth_llm_json", calls[0])

    def test_harvests_content_base64_and_strips_from_tool_output(self):
        import base64

        from sleuth.config import Config
        from sleuth.files.cos import MemoryObjectStore

        cfg = Config()
        cfg.cos.secret_id = "id"
        cfg.cos.secret_key = "key"
        cfg.cos.bucket = "b"
        cfg.cos.region = "ap-guangzhou"
        cfg.cos.path_prefix = "sleuth/files"
        cfg.files.require_encrypt = True
        cfg.files.sm4_key = "0123456789abcdef"
        mem = MemoryObjectStore()

        class Sess:
            id = "sess_bytes"
            user_id = "alice"
            config = cfg
            store = None
            _files = []
            _prompt_file_ids = None
            _turn_file_ids = []

        sess = Sess()
        encoded = base64.b64encode(b"hello-docx").decode("ascii")

        class FakeManager:
            def call_tool(
                self,
                qualified_name: str,
                arguments: dict,
                progress_callback=None,
            ):
                return (
                    json.dumps(
                        {
                            "ok": True,
                            "files": [
                                {
                                    "filename": "out.docx",
                                    "mime": "application/octet-stream",
                                    "object_key": "sleuth/files/out.docx",
                                    "content_base64": encoded,
                                    "size": 10,
                                }
                            ],
                        }
                    ),
                    False,
                )

        schema = {
            "type": "object",
            "properties": {"question": {"type": "string"}},
        }
        tool = McpBridgeTool(
            _info(
                server="ddcheck",
                name="check_report",
                qualified="ddcheck_check_report",
                schema=schema,
            ),
            FakeManager(),  # type: ignore[arg-type]
        )
        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=sess,
        )
        with patch(
            "sleuth.files.mailbox.object_store_from_config", return_value=mem
        ):
            result = tool.execute({"question": "x"}, ctx)
        self.assertFalse(result.is_error)
        payload = json.loads(result.output)
        self.assertNotIn("content_base64", json.dumps(payload))
        self.assertTrue(payload["files"][0].get("id"))
        self.assertTrue(sess._turn_file_ids)
        item = sess._files[0]
        self.assertTrue(item["encrypted"])
        self.assertIn("/alice/sess_bytes/", item["object_key"])
        self.assertIn(item["id"], item["object_key"])
        self.assertNotEqual(mem.get_bytes(item["object_key"]), b"hello-docx")

    def test_forwards_mcp_progress_to_session_renderer(self):
        from sleuth.config import Config

        events = []

        class Renderer:
            def on_progress(self, **kwargs):
                events.append(kwargs)

        class Sess:
            id = "sess_prog"
            user_id = "alice"
            config = Config()
            renderer = Renderer()
            store = None
            _files = []
            _turn_file_ids = []

        class FakeManager:
            def call_tool(
                self,
                qualified_name: str,
                arguments: dict,
                progress_callback=None,
            ):
                if progress_callback:
                    progress_callback(progress=1, total=4, message="kb")
                    progress_callback(progress=3, total=4, message="llm")
                return ('{"ok": true}', False)

        tool = McpBridgeTool(
            _info(
                server="ddcheck",
                name="check_report",
                qualified="ddcheck_check_report",
                schema={"type": "object", "properties": {"question": {"type": "string"}}},
            ),
            FakeManager(),  # type: ignore[arg-type]
        )
        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        result = tool.execute({"question": "x"}, ctx)
        self.assertFalse(result.is_error)
        stages = [e.get("stage") for e in events]
        self.assertIn("kb", stages)
        self.assertIn("llm", stages)


if __name__ == "__main__":
    unittest.main()
