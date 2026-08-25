"""Archive a memory the current user can access."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..memory.service import MemoryUnavailable, can_access_item, forget_memory
from ..memory.store import memory_store_for
from .base import ToolContext, ToolResult


class MemoryForgetParams(BaseModel):
    memory_id: str = Field(description="Memory id returned by memory_search or a previous write.")


class MemoryForgetTool:
    name = "memory_forget"
    description = (
        "Forget (archive) a long-term memory by id so it is no longer retrieved. "
        "Only memories in this user's user/role/org scopes can be forgotten."
    )
    params = MemoryForgetParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        session = ctx.session
        config = getattr(session, "config", None) if session is not None else None
        if config is None:
            return ToolResult.error(self.name, "session config is unavailable")
        memory_id = str(args.get("memory_id") or "").strip()
        if not memory_id:
            return ToolResult.error(self.name, "memory_id is required")
        store = memory_store_for(config)
        if store is None:
            return ToolResult.error(self.name, "long-term memory is not configured")
        item = store.get(memory_id)
        if item is None or not can_access_item(config, getattr(session, "user_id", "") or "", item):
            return ToolResult.error(self.name, "memory not found")
        try:
            forget_memory(config, memory_id, actor=getattr(session, "user_id", "") or "")
        except MemoryUnavailable as exc:
            return ToolResult.error(self.name, str(exc))
        except ValueError as exc:
            return ToolResult.error(self.name, str(exc))
        return ToolResult.success(self.name, f"forgot {memory_id}", id=memory_id)
