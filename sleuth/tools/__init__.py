"""Tool subsystem.

opencode tools are data: {id, description, parameters(Schema), execute}.
We port that with pydantic models for parameters (which also generate the
JSON schema we hand to the model) and a simple ToolContext carrying the
session/permission/cwd state each tool needs.
"""
from __future__ import annotations

from .base import Tool, ToolResult, ToolContext
from .registry import ToolRegistry

__all__ = ["Tool", "ToolResult", "ToolContext", "ToolRegistry"]
