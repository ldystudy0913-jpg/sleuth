"""人工介入：返回 need_input JSON。本文件一般不用改。

Sleuth 不解析这段 JSON。要暂停 HTTP 本轮，靠 SOP 让模型调内置 question。
开 HITL=1 只是让 should_pause 可能为真；缺什么必须由调用方传入 missing。
脚手架演示用「空 message」，真实业务在 mcp_server 包装函数里自己列缺项。
用户说没有补充后再传 proceed_with_gaps=true。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_HINT = (
    "向用户列出缺项，询问是否还有其他要补充的信息。"
    "有则下次带上字段再调用；用户说没有补充、请继续时再传 proceed_with_gaps=true。"
)


def coerce_bool(value: Any) -> bool:
    """把模型可能传来的 true/1/yes 收成 bool。"""
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
    """组装给模型看的缺料信封。基座不会因此自动暂停。"""
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
    """HITL 关闭或用户已 proceed_with_gaps 时不暂停；否则 missing 非空就暂停。"""
    if not enabled or coerce_bool(proceed_with_gaps):
        return False
    return any(str(item).strip() for item in (missing or []))
