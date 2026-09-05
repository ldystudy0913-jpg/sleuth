"""Pause the Sleuth turn when required inputs are missing.

Sleuth does not parse this JSON. The host model should call the built-in
``question`` tool so HTTP parks as ``awaiting_user``. Pass
``proceed_with_gaps=true`` only after the user says there is nothing more.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_HINT = (
    "向用户列出缺项，询问是否还有其他要补充的信息。"
    "有则下次带上字段再调用；用户说没有补充、请继续时再传 proceed_with_gaps=true。"
)


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value) and value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def need_input_payload(
    missing: Sequence[str],
    filled: Optional[Dict[str, Any]] = None,
    *,
    hint: Optional[str] = None,
) -> Dict[str, Any]:
    gaps: List[str] = [str(item).strip() for item in missing if str(item).strip()]
    return {
        "status": "need_input",
        "missing": gaps,
        "filled": dict(filled or {}),
        "hint": hint or DEFAULT_HINT,
    }


def should_pause(
    enabled: bool,
    missing: Optional[Iterable[str]] = None,
    proceed_with_gaps: Any = False,
) -> bool:
    if not enabled or coerce_bool(proceed_with_gaps):
        return False
    return any(str(item).strip() for item in (missing or []))
