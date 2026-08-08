"""检查图条件路由：解析后是否拉附件、打分后是否 LLM/HITL。"""
from __future__ import annotations

from ..models import FindingStatus
from .state import CheckState


def after_parse(state: CheckState) -> str:
    """parse_report 之后：策略需要附件维 → fetch，否则 skip。"""
    return "fetch_attachments" if state.get("need_attachments") else "skip_attachments"


def hitl_needed(state: CheckState) -> bool:
    """是否进入人工确认节点。

    - hitl_enabled 关 → 否
    - hitl_on_fail_only 开 → 仅当存在 FAIL finding
    - 否则 HITL 开就进入
    """
    if not state.get("hitl_enabled"):
        return False
    if not state.get("hitl_on_fail_only"):
        return True
    for f in state.get("findings") or []:
        status = getattr(f, "status", None)
        if status == FindingStatus.FAIL:
            return True
        if isinstance(f, dict) and str(f.get("status", "")).lower() == "fail":
            return True
    return False


def after_score(state: CheckState) -> str:
    """score 之后：优先 llm_summarize，否则 human_confirm 或 emit_result。"""
    if state.get("llm_enabled"):
        return "llm_summarize"
    return "human_confirm" if hitl_needed(state) else "emit_result"


def after_summary(state: CheckState) -> str:
    """llm_summarize 之后：HITL 或直接 emit。"""
    return "human_confirm" if hitl_needed(state) else "emit_result"
