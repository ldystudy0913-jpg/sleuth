"""Vector search over the current user's layered memories."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..memory.service import MemoryUnavailable, search_for_user
from .base import ToolContext, ToolResult


class MemorySearchParams(BaseModel):
    query: str = Field(description="Natural-language query to recall stored memories.")


class MemorySearchTool:
    name = "memory_search"
    description = (
        "Search long-term memories for this user (personal + their role + their org) "
        "by semantic similarity. Use when you need a stored preference, policy note, "
        "or workflow that is not in the current prompt."
    )
    params = MemorySearchParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        session = ctx.session
        config = getattr(session, "config", None) if session is not None else None
        if config is None:
            return ToolResult.error(self.name, "session config is unavailable")
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult.error(self.name, "query is required")
        user_id = getattr(session, "user_id", None) or ""
        try:
            hits = search_for_user(config, user_id, query)
        except MemoryUnavailable as exc:
            return ToolResult.error(self.name, str(exc))
        except Exception as exc:
            return ToolResult.error(self.name, f"{type(exc).__name__}: {exc}")
        if not hits:
            return ToolResult.success(self.name, "no matching memories")
        lines = []
        for item in hits:
            score = f"{item.score:.3f}" if item.score is not None else "-"
            lines.append(
                f"- {item.id} [{item.scope_kind}/{item.mem_kind}] {item.item_key} "
                f"(score={score}): {item.title_text}\n  {item.body_text}"
            )
        return ToolResult.success(self.name, "\n".join(lines), count=len(hits))
