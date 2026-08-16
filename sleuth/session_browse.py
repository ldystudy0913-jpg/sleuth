"""Session list / preview helpers for CLI and HTTP."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .messages import Message
from .session_select import skill_from_metadata
from .title import format_local_ms

_DEFAULT_PREVIEW_CHARS = 80


def truncate_preview(text: str, max_chars: int = _DEFAULT_PREVIEW_CHARS) -> str:
    """Single-line preview truncated to max_chars."""
    one = " ".join((text or "").split())
    if len(one) <= max_chars:
        return one
    if max_chars <= 3:
        return one[:max_chars]
    return one[: max_chars - 3] + "..."


def first_user_preview(
    messages: Sequence[Message],
    *,
    max_chars: int = _DEFAULT_PREVIEW_CHARS,
) -> str:
    """First non-empty user message text as preview."""
    for m in messages:
        if getattr(m, "role", None) != "user":
            continue
        text = (m.text or "").strip()
        if text:
            return truncate_preview(text, max_chars=max_chars)
    return ""


def build_session_list_rows(
    store: Any,
    *,
    user_id: str,
    limit: int = 20,
    preview_chars: int = _DEFAULT_PREVIEW_CHARS,
) -> List[Dict[str, Any]]:
    """List recent sessions with local time + first-user preview.

    Each row: index (1-based), id, title, agent, time_updated, time_updated_local, preview.
    """
    limit = max(1, min(int(limit or 20), 100))
    records = store.list_sessions(user_id=user_id, limit=limit)
    rows: List[Dict[str, Any]] = []
    for i, rec in enumerate(records, start=1):
        preview = ""
        try:
            msgs = store.load_messages(rec.id)
            preview = first_user_preview(msgs, max_chars=preview_chars)
        except Exception:
            preview = ""
        rows.append(
            {
                "index": i,
                "id": rec.id,
                "title": rec.title or "",
                "agent": rec.agent or "",
                "user_id": getattr(rec, "user_id", "") or "",
                "time_updated": rec.time_updated,
                "time_updated_local": format_local_ms(rec.time_updated),
                "preview": preview,
                "model": getattr(rec, "model", None),
                "skill": skill_from_metadata(getattr(rec, "metadata", None)),
                "cost": getattr(rec, "cost", 0),
                "tokens_input": getattr(rec, "tokens_input", 0),
                "tokens_output": getattr(rec, "tokens_output", 0),
            }
        )
    return rows


def resolve_session_id(
    rows: Sequence[Dict[str, Any]],
    ref: str,
    *,
    store: Any = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve list index (1-based), full id, or unique id prefix to session id."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        n = int(ref)
        for row in rows:
            if row.get("index") == n:
                return str(row["id"])
        return None
    # Exact match in recent list
    for row in rows:
        if row.get("id") == ref:
            return str(row["id"])
    # Unique prefix in recent list
    prefix_hits = [str(r["id"]) for r in rows if str(r.get("id", "")).startswith(ref)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    if len(prefix_hits) > 1:
        return None
    # Fall back to store lookup by exact id
    if store is not None:
        rec = store.get_session(ref)
        if rec is not None:
            if user_id and rec.user_id and rec.user_id != user_id:
                return None
            return rec.id
        # Prefix search among a wider list
        wider = store.list_sessions(user_id=user_id, limit=100) if user_id else []
        hits = [r.id for r in wider if r.id.startswith(ref)]
        if len(hits) == 1:
            return hits[0]
    return None
