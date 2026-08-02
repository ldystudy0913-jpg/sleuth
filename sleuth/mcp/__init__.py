"""MCP subsystem — remote tool servers."""
from __future__ import annotations

from .bridge import bridge_tools
from .manager import McpManager, get_manager, shutdown_manager

__all__ = [
    "McpManager",
    "bridge_tools",
    "get_manager",
    "shutdown_manager",
]
