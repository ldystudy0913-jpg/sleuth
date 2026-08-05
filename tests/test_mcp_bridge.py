"""Tests for MCP tool bridging: schema exposure and argument passthrough."""
from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock

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
            def call_tool(self, qualified_name: str, arguments: Dict[str, Any]):
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


if __name__ == "__main__":
    unittest.main()
