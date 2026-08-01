"""Tool registry.

Collects built-in tools, lets callers enable/disable by name (mirrors
opencode's `tools` config and the per-agent permission filtering), and
hands the final set to the session loop as provider specs + executables.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import Tool, ToolContext, ToolResult, to_provider_spec, validate_args
from .read import ReadTool
from .write import WriteTool
from .edit import EditTool
from .bash import BashTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .todo import TodoTool
from .question import QuestionTool
from .webfetch import WebFetchTool
from .task import TaskTool


def _builtins() -> List[Tool]:
    return [
        ReadTool(),
        WriteTool(),
        EditTool(),
        BashTool(),
        GlobTool(),
        GrepTool(),
        TodoTool(),
        QuestionTool(),
        WebFetchTool(),
        TaskTool(),
    ]


class ToolRegistry:
    """Holds tools by name and resolves the active set per agent."""

    def __init__(self, tools: Optional[List[Tool]] = None):
        self._tools: Dict[str, Tool] = {}
        for t in tools or _builtins():
            self._tools[t.name] = t

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._tools)

    def specs(
        self,
        enabled: Optional[List[str]] = None,
        disabled: Optional[List[str]] = None,
        permission_rules=None,
    ) -> List[dict]:
        """Provider-ready tool specs, optionally filtered.

        When `permission_rules` (a opencode Ruleset) is supplied, tools with a
        `deny *` rule are hidden from the model entirely (opencode
        `visibleTools`).
        """
        from ..permission import disabled_tools

        if permission_rules is not None:
            disabled = set(disabled or [])
            disabled |= disabled_tools(self.names(), permission_rules)

        out = []
        for name, tool in self._tools.items():
            if disabled and name in disabled:
                continue
            if enabled is not None and name not in enabled:
                continue
            out.append(to_provider_spec(tool))
        return out

    def execute(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        """Validate + run a tool by name. Errors become ToolResult.error so
        the model sees a structured refusal rather than a crash."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.error(name, f"unknown tool: {name}")
        parsed, err = validate_args(tool, args)
        if err is not None:
            return ToolResult.error(name, f"invalid arguments: {err}")
        try:
            return tool.execute(parsed or {}, ctx)
        except Exception as exc:  # surface tool failures to the model
            return ToolResult.error(name, f"{type(exc).__name__}: {exc}")
