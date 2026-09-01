"""Which MCP tools a session may see and execute.

Agent-typed servers (``agent: true``) are bound to the Agent Card name.
Generic servers stay available on every agent.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config
    from .manager import McpManager


def mcp_server_owner_agent(config: "Config", manager: "McpManager", server_name: str) -> Optional[str]:
    """Return the agent id that owns this MCP server, or None if it is generic."""
    name = (server_name or "").strip()
    if not name:
        return None
    srv = None
    for item in config.enabled_mcp_servers():
        if item.name == name:
            srv = item
            break
    if srv is None or not bool(getattr(srv, "agent", False)):
        return None
    for agent_name, mapped in (getattr(manager, "agent_card_servers", None) or {}).items():
        if mapped == name:
            return config.resolve_agent_name(agent_name)
    return config.resolve_agent_name(name)


def session_may_use_owner_agent(session, owner_agent: Optional[str]) -> bool:
    """Generic MCP (no owner) is always ok. Agent MCP requires matching session + grant."""
    if not owner_agent:
        return True
    cfg = getattr(session, "config", None)
    if cfg is None:
        return False
    current = cfg.resolve_agent_name(getattr(session, "agent_name", None) or "")
    owner = cfg.resolve_agent_name(owner_agent)
    if current != owner:
        return False
    from ..memory.acl import resource_allowed

    user_id = getattr(session, "user_id", None) or ""
    return resource_allowed(cfg, user_id, "agent", owner)
