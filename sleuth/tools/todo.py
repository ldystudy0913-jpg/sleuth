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


# A single shared todo store (per-process). opencode persists these to the
# session; for the MVP an in-memory singleton is enough to give the model
# working memory across turns within one run.
_STATE: List[dict] = []


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
        _STATE.clear()
        _STATE.extend(items)

        rendered = "\n".join(
            f"- [{'x' if t['status'] == 'completed' else ' '}] {t['content']}"
            + (" (in progress)" if t["status"] == "in_progress" else "")
            for t in items
        )
        return ToolResult.success("todo", rendered or "(empty todo list)", count=len(items))
