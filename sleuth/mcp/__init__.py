"""MCP subsystem — remote tool servers."""
from __future__ import annotations

from .agent_card import apply_agent_cards_to_config, parse_agent_card, sanitize_permissions
from .bridge import bridge_tools
from .manager import McpManager, get_manager, shutdown_manager

__all__ = [
    "McpManager",
    "bridge_tools",
    "get_manager",
    "shutdown_manager",
    "apply_agent_cards_to_config",
    "parse_agent_card",
    "sanitize_permissions",
]
