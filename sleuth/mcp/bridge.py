"""Bridge MCP tool descriptors into sleuth Tool objects."""
from __future__ import annotations

from typing import List, TYPE_CHECKING

from pydantic import BaseModel

from ..tools.base import ToolContext, ToolResult
from .manager import McpManager, McpToolInfo

if TYPE_CHECKING:
    from ..tools.base import Tool


class _McpPassthroughParams(BaseModel):
    """Placeholder params model for the Tool protocol.

    MCP tools skip pydantic validation and expose the server's original JSON
    Schema via ``parameters_json_schema``. Building dynamic models from MCP
    property names (e.g. ``schema``) shadows BaseModel attributes and is
    unnecessary.
    """


class McpBridgeTool:
    """A Tool facade that forwards execute to McpManager.call_tool."""

    skip_strict_validation = True

    def __init__(self, info: McpToolInfo, manager: McpManager):
        self.name = info.qualified
        self.description = info.description
        self.params = _McpPassthroughParams
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
