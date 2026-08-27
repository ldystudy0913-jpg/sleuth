"""Bridge MCP tool descriptors into sleuth Tool objects."""
from __future__ import annotations

import json
from typing import Any, Dict, List, TYPE_CHECKING

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


def _schema_props(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _inject_attachment_refs(args: dict, ctx: ToolContext, schema: Any) -> dict:
    props = _schema_props(schema)
    if "attachment_refs_json" not in props and "attachment_refs" not in props:
        return args
    session = ctx.session
    if session is None:
        return args
    from ..files.mailbox import attachment_refs

    refs = attachment_refs(
        config=session.config,
        session=session,
        file_ids=getattr(session, "_prompt_file_ids", None),
    )
    payload = json.dumps(refs, ensure_ascii=False)
    out = dict(args or {})
    if "attachment_refs_json" in props:
        out["attachment_refs_json"] = payload
    if "attachment_refs" in props:
        out["attachment_refs"] = refs
    return out


def _parse_json_object(text: str) -> Any:
    raw = (text or "").strip()
    if not raw.startswith("{") and not raw.startswith("["):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _harvest_files(text: str, ctx: ToolContext) -> List[Dict[str, Any]]:
    session = ctx.session
    if session is None:
        return []
    payload = _parse_json_object(text)
    if payload is None:
        return []
    from ..files.mailbox import harvest_tool_files

    atts = harvest_tool_files(session, payload)
    https_only: List[Dict[str, Any]] = []
    for att in atts:
        url = str(att.get("url") or "")
        if url.startswith("https://"):
            https_only.append(att)
    return https_only


def _harvest_sources(text: str) -> List[Dict[str, Any]]:
    payload = _parse_json_object(text)
    if payload is None:
        return []
    from ..sources import harvest_tool_sources

    return harvest_tool_sources(payload)


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
        forwarded = _inject_attachment_refs(args or {}, ctx, self.parameters_json_schema)
        text, is_error = self._manager.call_tool(self.name, forwarded)
        if is_error:
            return ToolResult.error(self.name, text, server=self._info.server)
        attachments = _harvest_files(text, ctx)
        sources = _harvest_sources(text)
        extra: Dict[str, Any] = {}
        if sources:
            extra["sources"] = sources
        return ToolResult.success(
            self.name,
            text,
            server=self._info.server,
            attachments=attachments,
            **extra,
        )


def bridge_tools(manager: McpManager) -> List["Tool"]:
    return [McpBridgeTool(info, manager) for info in manager.tools.values()]
