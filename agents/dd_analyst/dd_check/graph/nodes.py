"""尽调检查图各节点实现（按 build.py 中的顺序执行）。

状态在 CheckState 里流转；每个节点返回要合并进 state 的字段补丁。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from ..adapter import ReportAdapter
from ..attachments import AttachmentPipeline
from ..attachments.cos_client import CosObjectStore
from ..attachments.mysql_meta import MysqlDdpFileStore
from ..config import Settings
from ..models import CheckRequest, CheckResult, FindingStatus
from ..rules import RuleContext, RuleEngine
from ..scoring import aggregate_score
from ..strategy import StrategyResolver
from .state import CheckState


def _trace(state: CheckState, name: str, started: float, **extra: Any) -> List[Dict[str, Any]]:
    """追加一条节点耗时记录，便于结果里的 metadata.trace 审计。"""
    row = {"node": name, "ms": round((time.perf_counter() - started) * 1000, 2)}
    row.update(extra)
    return list(state.get("trace") or []) + [row]


def _attachment_pipeline(settings: Settings) -> AttachmentPipeline:
    """按配置挂 MySQL 元数据 + COS；未配置则用空实现（跳过下载）。"""
    meta = MysqlDdpFileStore(settings)
    obj = CosObjectStore(settings)
    return AttachmentPipeline(
        settings,
        meta_store=meta if meta.configured() else None,
        object_store=obj if obj.configured() else None,
    )


def ingest_normalize(state: CheckState) -> Dict[str, Any]:
    """入口：归一化 custType/phase，写入 HITL/LLM 开关到 state。"""
    t0 = time.perf_counter()
    settings: Settings = state["settings"]
    req: CheckRequest = state["request"]
    resolver = StrategyResolver(settings)
    cust = resolver.normalize_cust_type(req.custType)
    phase = resolver.normalize_phase(req.phase)
    llm_enabled = bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)
    return {
        "cust_type": cust.value,
        "phase": phase.value,
        "llm_enabled": llm_enabled,
        "hitl_enabled": bool(settings.hitl_enabled),
        "hitl_on_fail_only": bool(settings.hitl_on_fail_only),
        "skipped_attachments": [],
        "findings": [],
        "errors": list(state.get("errors") or []),
        "trace": _trace(
            state,
            "ingest_normalize",
            t0,
            cust=cust.value,
            phase=phase.value,
            hitl=bool(settings.hitl_enabled),
        ),
    }


def resolve_strategy(state: CheckState) -> Dict[str, Any]:
    """选策略模板与启用检查维度；是否需要附件由此决定。"""
    t0 = time.perf_counter()
    settings: Settings = state["settings"]
    req: CheckRequest = state["request"]
    resolver = StrategyResolver(settings)
    cust = resolver.normalize_cust_type(state.get("cust_type") or req.custType)
    phase = resolver.normalize_phase(state.get("phase") or req.phase)
    strategy = resolver.resolve(req.busCode, cust, phase)
    dims = strategy.enabled_for(cust)
    need_attach = any(d.startswith("attachment_") for d in dims)
    return {
        "strategy": strategy,
        "enabled_dimensions": dims,
        "need_attachments": need_attach,
        "cust_type": cust.value,
        "phase": phase.value,
        "trace": _trace(state, "resolve_strategy", t0, strategy_id=strategy.id, dims=len(dims)),
    }


def parse_report(state: CheckState) -> Dict[str, Any]:
    """把业务字段 result（表单 JSON 字符串）解析成结构化 facts。"""
    t0 = time.perf_counter()
    req: CheckRequest = state["request"]
    facts = ReportAdapter().parse(req.result)
    return {
        "facts": facts,
        "trace": _trace(state, "parse_report", t0, sections=facts.raw_section_count),
    }


def fetch_attachments(state: CheckState) -> Dict[str, Any]:
    """按 investId 查元数据 → 下载 → SM4 解密 → 文本摘要；失败写入 skipped，不抛崩。"""
    t0 = time.perf_counter()
    settings: Settings = state["settings"]
    req: CheckRequest = state["request"]
    bundle = _attachment_pipeline(settings).run(req.investId)
    return {
        "attachments": bundle,
        "skipped_attachments": list(bundle.skipped),
        "trace": _trace(state, "fetch_attachments", t0, skipped=len(bundle.skipped)),
    }


def skip_attachments(state: CheckState) -> Dict[str, Any]:
    """策略不含附件维时走此空节点。"""
    t0 = time.perf_counter()
    return {
        "attachments": None,
        "skipped_attachments": list(state.get("skipped_attachments") or []),
        "trace": _trace(state, "skip_attachments", t0),
    }


def run_rule_dims(state: CheckState) -> Dict[str, Any]:
    """对启用维度调用 RuleEngine，产出 findings 列表。"""
    t0 = time.perf_counter()
    settings: Settings = state["settings"]
    req: CheckRequest = state["request"]
    resolver = StrategyResolver(settings)
    cust = resolver.normalize_cust_type(state["cust_type"])
    phase = resolver.normalize_phase(state["phase"])
    dims = list(state.get("enabled_dimensions") or [])
    ctx = RuleContext(
        facts=state["facts"],
        settings=settings,
        cust_type=cust,
        phase=phase,
        current_datetime=req.effective_datetime(),
        approve_data=req.approveData,
        attachments=state.get("attachments"),
        question=req.question,
    )
    findings = RuleEngine(settings).run(dims, ctx)
    return {
        "findings": findings,
        "trace": _trace(state, "run_rule_dims", t0, finding_count=len(findings)),
    }


def score_aggregate(state: CheckState) -> Dict[str, Any]:
    """按维度权重汇总 score / grade / summary。"""
    t0 = time.perf_counter()
    settings: Settings = state["settings"]
    dims = list(state.get("enabled_dimensions") or [])
    findings = list(state.get("findings") or [])
    score, grade, dim_scores, summary = aggregate_score(findings, dims, settings)
    return {
        "score": score,
        "grade": grade,
        "dimension_scores": dim_scores,
        "summary": summary,
        "trace": _trace(state, "score_aggregate", t0, score=score, grade=grade),
    }


def llm_summarize(state: CheckState) -> Dict[str, Any]:
    """可选语义摘要钩子；未接真实 LLM 时仅标记规则摘要。"""
    t0 = time.perf_counter()
    summary = state.get("summary") or ""
    if summary and not summary.endswith("（规则摘要）"):
        summary = summary + "（规则摘要）"
    return {
        "summary": summary,
        "trace": _trace(state, "llm_summarize", t0, skipped_impl=True),
    }


def _normalize_decision(raw: Any) -> Dict[str, Any]:
    """把 interrupt 返回值统一成 {action, ...}。"""
    if isinstance(raw, str):
        return {"action": raw.lower().strip() or "approve"}
    if isinstance(raw, dict):
        action = str(raw.get("action") or raw.get("type") or "approve").lower().strip()
        out = dict(raw)
        out["action"] = action
        return out
    return {"action": "approve"}


def human_confirm(state: CheckState) -> Dict[str, Any]:
    """HITL：interrupt 把 findings 预览交给 Sleuth 侧人工确认，resume 后继续。

    action: approve | edit_summary | reject
    """
    from langgraph.types import interrupt

    t0 = time.perf_counter()
    req: CheckRequest = state["request"]
    findings = list(state.get("findings") or [])
    preview = []
    for f in findings:
        status = getattr(f, "status", None)
        status_v = status.value if hasattr(status, "value") else str(status or "")
        if status_v not in {"fail", "warn", "FAIL", "WARN"} and str(status_v).lower() not in {
            "fail",
            "warn",
        }:
            continue
        preview.append(
            {
                "dimension": getattr(f, "dimension", ""),
                "status": str(status_v).lower(),
                "message": getattr(f, "message", "")[:200],
            }
        )
        if len(preview) >= 20:
            break

    payload = {
        "type": "dd_confirm",
        "action_request": {
            "action": "confirm_check",
            "args": {
                "reportId": req.reportId,
                "score": state.get("score"),
                "grade": state.get("grade"),
            },
        },
        "config": {
            "allow_accept": True,
            "allow_edit": True,
            "allow_respond": True,
            "allow_ignore": True,
        },
        "reportId": req.reportId,
        "investId": req.investId,
        "score": state.get("score"),
        "grade": state.get("grade"),
        "summary": state.get("summary") or "",
        "findings_preview": preview,
        "actions": ["approve", "edit_summary", "reject"],
        "message": (
            "Review due-diligence check results. "
            "approve = accept; edit_summary = provide summary text; reject = mark rejected."
        ),
    }
    raw = interrupt(payload)  # 图在此暂停，等 Command(resume=...)
    decision = _normalize_decision(raw)
    action = decision.get("action") or "approve"
    if action in {"accept", "approve"}:
        action = "approve"
    elif action in {"edit", "edit_summary"}:
        action = "edit_summary"
    elif action in {"ignore", "reject"}:
        action = "reject"
    elif action == "response":
        action = "edit_summary"
        if "summary" not in decision and decision.get("args"):
            decision["summary"] = str(decision.get("args"))

    updates: Dict[str, Any] = {
        "human_decision": {**decision, "action": action},
        "trace": _trace(state, "human_confirm", t0, action=action),
    }
    if action == "edit_summary":
        new_summary = (
            decision.get("summary")
            or decision.get("content")
            or decision.get("feedback")
            or ""
        )
        if new_summary:
            updates["summary"] = str(new_summary)
        updates["human_status"] = "edited_by_human"
    elif action == "reject":
        updates["human_status"] = "rejected_by_human"
        base = state.get("summary") or ""
        feedback = decision.get("feedback") or decision.get("content") or ""
        updates["summary"] = (
            f"{base} 【人工驳回】{feedback}".strip()
            if feedback
            else f"{base} 【人工驳回】".strip()
        )
    else:
        updates["human_status"] = "approved_by_human"
    return updates


def emit_result(state: CheckState) -> Dict[str, Any]:
    """终点：打成 CheckResult 字典（含 findings/score/trace/人工状态）。"""
    t0 = time.perf_counter()
    req: CheckRequest = state["request"]
    strategy = state.get("strategy")
    findings = list(state.get("findings") or [])
    facts = state.get("facts")
    human_status = state.get("human_status") or ""
    human_decision = state.get("human_decision")
    result = CheckResult(
        reportId=req.reportId,
        investId=req.investId,
        busCode=req.busCode,
        custType=state.get("cust_type") or "",
        phase=state.get("phase") or "",
        strategy_id=strategy.id if strategy else "",
        enabled_dimensions=list(state.get("enabled_dimensions") or []),
        findings=findings,
        score=float(state.get("score") or 0),
        grade=str(state.get("grade") or "E"),
        dimension_scores=list(state.get("dimension_scores") or []),
        summary=str(state.get("summary") or ""),
        skipped_attachments=list(state.get("skipped_attachments") or []),
        metadata={
            "bankId": req.bankId,
            "busCodeDesc": req.busCodeDesc,
            "question": req.question,
            "section_count": facts.raw_section_count if facts else 0,
            "fail_count": sum(1 for f in findings if f.status == FindingStatus.FAIL),
            "warn_count": sum(1 for f in findings if f.status == FindingStatus.WARN),
            "trace": list(state.get("trace") or []),
            "human_status": human_status or None,
            "human_decision": human_decision,
        },
    )
    payload = result.model_dump()
    payload["trace"] = list(state.get("trace") or []) + [
        {"node": "emit_result", "ms": round((time.perf_counter() - t0) * 1000, 2)}
    ]
    if human_status:
        payload["status"] = (
            "rejected" if human_status == "rejected_by_human" else "completed"
        )
    return {
        "result": payload,
        "trace": payload["trace"],
    }
