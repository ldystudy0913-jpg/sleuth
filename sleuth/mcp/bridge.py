"""Bridge MCP tool descriptors into sleuth Tool objects."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, create_model

from ..tools.base import ToolContext, ToolResult
from .manager import McpManager, McpToolInfo

if TYPE_CHECKING:
    from ..tools.base import Tool


class _EmptyParams(BaseModel):
    """Fallback when MCP tool has no input schema properties."""


def _params_model_for(info: McpToolInfo) -> type:
    """Build a loose pydantic model from JSON Schema properties (best-effort)."""
    schema = info.input_schema or {}
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        return _EmptyParams
    fields: Dict[str, Any] = {}
    required = set(schema.get("required") or [])
    for key, spec in props.items():
        if not isinstance(key, str):
            continue
        default = ... if key in required else None
        fields[key] = (Any, default)
    if not fields:
        return _EmptyParams
    try:
        return create_model(f"McpParams_{info.qualified}", **fields)  # type: ignore[call-overload]
    except Exception:
        return _EmptyParams


class McpBridgeTool:
    """A Tool facade that forwards execute to McpManager.call_tool."""

    skip_strict_validation = True

    def __init__(self, info: McpToolInfo, manager: McpManager):
        self.name = info.qualified
        self.description = info.description
        self.params = _params_model_for(info)
        self.parameters_json_schema = info.input_schema or {
            "type": "object",
            "properties": {},
        }
        self._info = info
        self._manager = manager

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        text, is_error = self._manager.call_tool(self.name, args or {})
        if is_error:
            return ToolResult.error(self.name, text, server=self._info.server)
        return ToolResult.success(self.name, text, server=self._info.server)


def bridge_tools(manager: McpManager) -> List["Tool"]:
    return [McpBridgeTool(info, manager) for info in manager.tools.values()]
