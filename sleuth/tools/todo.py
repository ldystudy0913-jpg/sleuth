"""Todo tool — structured task tracking, like opencode's TodoWrite."""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult


class TodoItem(BaseModel):
    content: str = Field(description="The task text.")
    status: str = Field(
        description='One of "pending", "in_progress", "completed", "cancelled".'
    )


class TodoParams(BaseModel):
    todos: List[TodoItem] = Field(description="The full updated todo list.")


_STATE: List[dict] = []


def set_state(todos: List[dict]) -> None:
    _STATE.clear()
    _STATE.extend(list(todos or []))


def get_state() -> List[dict]:
    return list(_STATE)


class TodoTool:
    name = "todo"
    description = (
        "Maintain a structured todo list for the current task. Use it whenever "
        "the work involves 3 or more distinct steps. Set exactly one item to "
        "in_progress at a time, and mark items completed as soon as they are "
        "done so the user can see progress."
    )
    params = TodoParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = TodoParams(**args)
        items = [t.model_dump() for t in p.todos]
        set_state(items)

        session = getattr(ctx, "session", None)
        store = getattr(session, "store", None) if session is not None else None
        session_id = getattr(ctx, "session_id", None)
        if store is not None and session_id and hasattr(store, "save_todos"):
            try:
                store.save_todos(session_id, items)
            except Exception as exc:
                return ToolResult.error("todo", f"persist failed: {exc}")

        rendered = "\n".join(
            f"- [{'x' if t['status'] == 'completed' else ' '}] {t['content']}"
            + (" (in progress)" if t["status"] == "in_progress" else "")
            for t in items
        )
        return ToolResult.success("todo", rendered or "(empty todo list)", count=len(items))
