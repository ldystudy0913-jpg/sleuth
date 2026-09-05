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


def _session_llm_payload(session: Any) -> Dict[str, str]:
    provider = getattr(session, "provider", None)
    model = str(getattr(session, "model_id", "") or "").strip()
    api_key = str(getattr(provider, "api_key", "") or "").strip() if provider is not None else ""
    base_url = str(getattr(provider, "base_url", "") or "").strip() if provider is not None else ""
    if not base_url:
        base_url = "https://api.openai.com/v1"
    if not model or not api_key:
        return {}
    return {
        "model": model,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
    }


def _inject_sleuth_llm_json(args: dict, ctx: ToolContext, schema: Any) -> dict:
    props = _schema_props(schema)
    if "sleuth_llm_json" not in props:
        return args
    session = ctx.session
    if session is None:
        return args
    payload = _session_llm_payload(session)
    if not payload:
        return args
    out = dict(args or {})
    out["sleuth_llm_json"] = json.dumps(payload, ensure_ascii=False)
    return out


def _inject_mcp_args(args: dict, ctx: ToolContext, schema: Any) -> dict:
    forwarded = _inject_attachment_refs(args or {}, ctx, schema)
    return _inject_sleuth_llm_json(forwarded, ctx, schema)


def _parse_json_object(text: str) -> Any:
    raw = (text or "").strip()
    if not raw.startswith("{") and not raw.startswith("["):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


_FILE_BODY_KEYS = ("content", "content_base64", "contentBase64")


def _harvest_files(text: str, ctx: ToolContext) -> List[Dict[str, Any]]:
    session = ctx.session
    if session is None:
        return []
    payload = _parse_json_object(text)
    if payload is None:
        return []
    from ..files.mailbox import harvest_tool_files

    return harvest_tool_files(session, payload)


def _redact_tool_file_payloads(text: str, harvested: List[Dict[str, Any]]) -> str:
    payload = _parse_json_object(text)
    if not isinstance(payload, dict):
        return text
    files = payload.get("files")
    if not isinstance(files, list):
        return text
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for item in harvested:
        if not isinstance(item, dict):
            continue
        by_name.setdefault(str(item.get("filename") or ""), []).append(item)
    new_files: List[Any] = []
    changed = False
    for entry in files:
        if not isinstance(entry, dict):
            new_files.append(entry)
            continue
        item = {k: v for k, v in entry.items() if k not in _FILE_BODY_KEYS}
        if item != entry:
            changed = True
        name = str(item.get("filename") or item.get("name") or "")
        queue = by_name.get(name) or []
        if queue:
            hit = queue.pop(0)
            fid = str(hit.get("id") or "").strip()
            if fid and item.get("id") != fid:
                item["id"] = fid
                changed = True
        new_files.append(item)
    if not changed:
        return text
    out = dict(payload)
    out["files"] = new_files
    return json.dumps(out, ensure_ascii=False)


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
        self.owner_agent = None

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        session = getattr(ctx, "session", None)
        owner = getattr(self, "owner_agent", None)
        if owner is None and session is not None and self._manager is not None:
            from .access import mcp_server_owner_agent

            owner = mcp_server_owner_agent(
                session.config, self._manager, self._info.server
            )
        if session is not None:
            from .access import session_may_use_owner_agent

            if not session_may_use_owner_agent(session, owner):
                return ToolResult.error(
                    self.name,
                    "permission denied: agent MCP not allowed for this session",
                    server=self._info.server,
                )
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        forwarded = _inject_mcp_args(args or {}, ctx, self.parameters_json_schema)

        def _progress(progress=None, total=None, message=None):
            if session is None:
                return
            from ..progress import emit_progress

            emit_progress(
                session,
                stage=str(message or self.name),
                detail=str(message or ""),
                progress=progress,
                total=total,
            )

        text, is_error = self._manager.call_tool(
            self.name, forwarded, progress_callback=_progress
        )
        if is_error:
            return ToolResult.error(self.name, text, server=self._info.server)
        harvested = _harvest_files(text, ctx)
        text = _redact_tool_file_payloads(text, harvested)
        attachments: List[Dict[str, Any]] = []
        for att in harvested:
            url = str(att.get("url") or "")
            if url.startswith("https://"):
                attachments.append(att)
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


def bridge_tools(manager: McpManager, session=None) -> List["Tool"]:
    tools = [McpBridgeTool(info, manager) for info in manager.tools.values()]
    if session is None:
        return tools
    from .access import mcp_server_owner_agent, session_may_use_owner_agent

    kept: List["Tool"] = []
    cfg = getattr(session, "config", None)
    for tool in tools:
        owner = None
        if cfg is not None:
            owner = mcp_server_owner_agent(cfg, manager, tool._info.server)
        tool.owner_agent = owner
        if session_may_use_owner_agent(session, owner):
            kept.append(tool)
    return kept
