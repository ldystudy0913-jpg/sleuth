"""Project persisted messages into a Trajectory-style execution ledger."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .messages import Message, ToolResultBlock
from .session_browse import truncate_preview


def now_ms() -> int:
    return int(time.time() * 1000)


def _opt_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _duration_ms(started_at: Optional[int], completed_at: Optional[int]) -> Optional[int]:
    if started_at is None or completed_at is None:
        return None
    return max(0, int(completed_at) - int(started_at))


def project_session_trace(
    messages: List[Message],
    *,
    session_id: str,
    preview_chars: int = 80,
) -> Dict[str, Any]:
    """Fold user / assistant / tool-result messages into ordered ledger records."""
    records: List[Dict[str, Any]] = []
    seq = 0
    tool_names: Dict[str, str] = {}

    for msg in messages or []:
        meta = dict(getattr(msg, "metadata", None) or {})
        message_id = meta.get("id")
        role = getattr(msg, "role", "") or ""
        results = [
            b for b in (getattr(msg, "content", None) or [])
            if isinstance(b, ToolResultBlock)
        ]

        if role == "assistant":
            for tu in getattr(msg, "tool_uses", []) or []:
                if getattr(tu, "id", None):
                    tool_names[str(tu.id)] = getattr(tu, "name", "") or ""
            seq += 1
            started_at = _opt_int(meta.get("started_at"))
            first_token_at = _opt_int(meta.get("first_token_at"))
            completed_at = _opt_int(meta.get("completed_at"))
            duration_ms = _opt_int(meta.get("duration_ms"))
            if duration_ms is None:
                duration_ms = _duration_ms(started_at, completed_at)
            preview_src = (getattr(msg, "text", None) or "") or (
                getattr(msg, "reasoning", None) or ""
            )
            records.append(
                {
                    "kind": "message",
                    "seq": seq,
                    "message_id": message_id,
                    "step": _opt_int(meta.get("step")),
                    "started_at": started_at,
                    "first_token_at": first_token_at,
                    "completed_at": completed_at,
                    "duration_ms": duration_ms,
                    "usage": meta.get("usage"),
                    "preview": truncate_preview(preview_src, max_chars=preview_chars),
                }
            )
            continue

        if results:
            spans = meta.get("tool_spans") or []
            spans_by_id: Dict[str, Dict[str, Any]] = {}
            if isinstance(spans, list):
                for item in spans:
                    if isinstance(item, dict) and item.get("id"):
                        spans_by_id[str(item["id"])] = item
            for block in results:
                seq += 1
                call_id = str(getattr(block, "tool_use_id", "") or "")
                span = spans_by_id.get(call_id) or {}
                started_at = _opt_int(span.get("started_at"))
                ended_at = _opt_int(span.get("ended_at"))
                duration_ms = _opt_int(span.get("duration_ms"))
                if duration_ms is None:
                    duration_ms = _duration_ms(started_at, ended_at)
                name = (
                    str(span.get("name") or "").strip()
                    or tool_names.get(call_id)
                    or ""
                )
                preview_src = name or (getattr(block, "content", None) or "")
                records.append(
                    {
                        "kind": "tool",
                        "seq": seq,
                        "message_id": message_id,
                        "id": call_id or None,
                        "name": name or None,
                        "started_at": started_at,
                        "duration_ms": duration_ms,
                        "ended_at": ended_at,
                        "is_error": _opt_bool(
                            span["is_error"] if "is_error" in span else getattr(block, "is_error", None)
                        ),
                        "preview": truncate_preview(str(preview_src), max_chars=preview_chars),
                    }
                )
            continue

        if role == "user":
            seq += 1
            records.append(
                {
                    "kind": "user",
                    "seq": seq,
                    "message_id": message_id,
                    "started_at": _opt_int(meta.get("started_at")),
                    "preview": truncate_preview(
                        getattr(msg, "text", None) or "", max_chars=preview_chars
                    ),
                }
            )

    return {"session_id": session_id, "records": records}


def message_timing_fields(metadata: Any) -> Dict[str, Any]:
    """Timing fields to attach on GET /v1/sessions/{id} message rows."""
    meta = metadata if isinstance(metadata, dict) else {}
    started_at = _opt_int(meta.get("started_at"))
    first_token_at = _opt_int(meta.get("first_token_at"))
    completed_at = _opt_int(meta.get("completed_at"))
    duration_ms = _opt_int(meta.get("duration_ms"))
    if duration_ms is None:
        duration_ms = _duration_ms(started_at, completed_at)
    return {
        "step": _opt_int(meta.get("step")),
        "started_at": started_at,
        "first_token_at": first_token_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
    }
