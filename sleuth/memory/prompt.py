"""Format recalled memories for the system prompt."""
from __future__ import annotations

from typing import List, Optional

from .models import MemoryItem
from .resolve import retrieve_for_prompt


def format_memory_block(items: List[MemoryItem]) -> str:
    if not items:
        return ""
    lines = [
        "# Long-term memory",
        "Use these stored notes when they are relevant. "
        "Items marked forget are negative constraints — do not do what they forbid. "
        "Do not invent extra memories. Do not echo raw identity numbers.",
        "",
    ]
    for item in items:
        title = (item.title_text or "").strip()
        body = (item.body_text or "").strip()
        head = f"- [{item.scope_kind}/{item.mem_kind}] {item.item_key}"
        if title:
            head += f": {title}"
        lines.append(head)
        if body:
            lines.append(f"  {body}")
    return "\n".join(lines).strip()


def memory_prompt_block(session, query: Optional[str] = None) -> str:
    config = getattr(session, "config", None)
    if config is None:
        return ""
    user_id = getattr(session, "user_id", None) or ""
    text = (query or "").strip()
    if not text:
        for msg in reversed(getattr(session, "messages", None) or []):
            if getattr(msg, "role", "") == "user":
                getter = getattr(msg, "text", None)
                text = (getter() if callable(getter) else str(getter or "")).strip()
                break
    try:
        items = retrieve_for_prompt(config, user_id, text)
    except Exception:
        return ""
    return format_memory_block(items)
