"""检查图的调用入口：同步跑完 / HITL 启动与续跑 / 批量 / list / rollback。

MCP：run_dd_check → start_check；resume_dd_check → resume_check；
list_dd_checkpoints / rollback_dd_check → list_checkpoints / rollback_check。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Union

from langgraph.types import Command

from ..config import Settings, get_settings
from ..models import BatchCheckRequest, CheckRequest, CheckResult, FindingStatus
from .build import build_check_graph, describe_graph
from .checkpoint import close_checkpointer, get_sqlite_checkpointer
from .state import CheckState

_GRAPH_SYNC = None
_GRAPH_HITL = None
_GRAPH_SYNC_WITH_CP = None
_ACTIVE_CHECKPOINTER = None


def _interrupt_value(out: Dict[str, Any]) -> Optional[Any]:
    """从 LangGraph invoke 返回值里取出 interrupt 载荷（若有）。"""
    raw = out.get("__interrupt__")
    if not raw:
        return None
    first = raw[0]
    return getattr(first, "value", first)


def _settings_dict(settings: Settings) -> Dict[str, Any]:
    return {k: getattr(settings, k) for k in settings.__dict__}


def _require_checkpoint_path(settings: Settings) -> None:
    if not settings.checkpoint_sqlite_path:
        raise RuntimeError(
            "DD_CHECK_HITL=1 requires DD_CHECK_CHECKPOINT_SQLITE_PATH "
            "(apply deploy/ddl_langgraph_checkpoint.sql first; no in-memory checkpointer)"
        )


def _resolve_checkpointer(settings: Settings):
    """若配置了 path 则返回持久 saver，否则 None。"""
    global _ACTIVE_CHECKPOINTER
    path = settings.checkpoint_sqlite_path
    if not path:
        return None
    _ACTIVE_CHECKPOINTER = get_sqlite_checkpointer(path)
    return _ACTIVE_CHECKPOINTER


def get_graph(*, hitl: bool = False, settings: Optional[Settings] = None):
    """获取（或惰性编译）同步图 / HITL 图。

    - 有 checkpoint path：两张图都挂同一 SqliteSaver
    - HITL 且无 path：报错
    - 同步且无 path：无 checkpointer
    """
    global _GRAPH_SYNC, _GRAPH_HITL, _GRAPH_SYNC_WITH_CP
    settings = settings or get_settings()
    cp = _resolve_checkpointer(settings)

    if hitl:
        if cp is None:
            _require_checkpoint_path(settings)
        if _GRAPH_HITL is None:
            _GRAPH_HITL = build_check_graph(hitl=True, checkpointer=cp)
        return _GRAPH_HITL

    if cp is not None:
        if _GRAPH_SYNC_WITH_CP is None:
            _GRAPH_SYNC_WITH_CP = build_check_graph(hitl=False, checkpointer=cp)
        return _GRAPH_SYNC_WITH_CP

    if _GRAPH_SYNC is None:
        _GRAPH_SYNC = build_check_graph(hitl=False, checkpointer=None)
    return _GRAPH_SYNC


def reset_graphs() -> None:
    """测试用：清掉缓存图与 checkpointer 连接。"""
    global _GRAPH_SYNC, _GRAPH_HITL, _GRAPH_SYNC_WITH_CP, _ACTIVE_CHECKPOINTER
    _GRAPH_SYNC = None
    _GRAPH_HITL = None
    _GRAPH_SYNC_WITH_CP = None
    _ACTIVE_CHECKPOINTER = None
    close_checkpointer()


def _initial_state(req: CheckRequest, settings: Settings) -> CheckState:
    """构造图的初始 state（请求 + 配置 + 空 findings/trace）。"""
    return {
        "request": req,
        "settings": settings,
        "trace": [],
        "errors": [],
        "findings": [],
        "skipped_attachments": [],
    }


def _format_invoke_out(
    out: Dict[str, Any],
    *,
    thread_id: str,
    report_id: str = "",
) -> Dict[str, Any]:
    interrupt_payload = _interrupt_value(out)
    if interrupt_payload is not None:
        return {
            "status": "awaiting_human",
            "thread_id": thread_id,
            "interrupt": interrupt_payload,
            "reportId": report_id or out.get("request") and getattr(out.get("request"), "reportId", ""),
            "score": out.get("score"),
            "grade": out.get("grade"),
            "summary": out.get("summary"),
            "findings_preview": (
                interrupt_payload.get("findings_preview")
                if isinstance(interrupt_payload, dict)
                else None
            ),
        }
    payload = out.get("result")
    if not payload:
        raise RuntimeError("check graph did not emit result")
    return {"status": payload.get("status") or "completed", "thread_id": thread_id, **payload}


def invoke_check(req: CheckRequest, settings: Optional[Settings] = None) -> CheckResult:
    """同步检查并返回强类型 CheckResult（供 Orchestrator / 单测）。"""
    payload = invoke_check_dict(req, settings)
    data = {k: v for k, v in payload.items() if k not in {"trace", "status", "thread_id"}}
    return CheckResult.model_validate(data)


def invoke_check_dict(req: CheckRequest, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """同步跑完整张图并返回结果字典（强制关闭 HITL，保证一次跑完）。"""
    settings = settings or get_settings()
    sync_settings = settings
    if settings.hitl_enabled:
        sync_settings = Settings(
            **{
                **_settings_dict(settings),
                "hitl_enabled": False,
            }
        )
    graph = get_graph(hitl=False, settings=sync_settings)
    state = _initial_state(req, sync_settings)
    if sync_settings.checkpoint_sqlite_path:
        tid = str(uuid.uuid4())
        final = graph.invoke(state, {"configurable": {"thread_id": tid}})
        payload = final.get("result")
        if not payload:
            raise RuntimeError("check graph did not emit result")
        payload = dict(payload)
        payload["thread_id"] = tid
        return payload
    final = graph.invoke(state)
    payload = final.get("result")
    if not payload:
        raise RuntimeError("check graph did not emit result")
    return payload


def start_check(
    req: CheckRequest,
    settings: Optional[Settings] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """MCP run_dd_check 入口。

    - HITL 关：completed（若配了 checkpoint path 仍返回 thread_id）
    - HITL 开：可能 awaiting_human；必须已配 checkpoint path
    """
    settings = settings or get_settings()
    if not settings.hitl_enabled:
        payload = invoke_check_dict(req, settings)
        return {"status": "completed", **payload}

    _require_checkpoint_path(settings)
    tid = thread_id or str(uuid.uuid4())
    graph = get_graph(hitl=True, settings=settings)
    config = {"configurable": {"thread_id": tid}}
    out = graph.invoke(_initial_state(req, settings), config)
    return _format_invoke_out(out, thread_id=tid, report_id=req.reportId)


def resume_check(
    thread_id: str,
    decision: Union[str, Dict[str, Any]],
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """MCP resume_dd_check：用同一 thread_id 把人工决定喂回 interrupt 节点。"""
    settings = settings or get_settings()
    if not settings.hitl_enabled:
        return {
            "status": "error",
            "error": "HITL is disabled (DD_CHECK_HITL=0); nothing to resume",
        }
    _require_checkpoint_path(settings)
    if isinstance(decision, str):
        try:
            decision = json.loads(decision)
        except json.JSONDecodeError:
            decision = {"action": decision}

    graph = get_graph(hitl=True, settings=settings)
    config = {"configurable": {"thread_id": thread_id}}
    out = graph.invoke(Command(resume=decision), config)
    return _format_invoke_out(out, thread_id=thread_id)


def list_checkpoints(
    thread_id: str,
    settings: Optional[Settings] = None,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    """列出某 thread 的 checkpoint 历史（新→旧），供 MCP list_dd_checkpoints。"""
    settings = settings or get_settings()
    if not settings.checkpoint_sqlite_path:
        return {
            "status": "error",
            "error": "DD_CHECK_CHECKPOINT_SQLITE_PATH not set; no durable checkpoints",
            "thread_id": thread_id,
            "checkpoints": [],
        }
    # HITL 开用 HITL 图；否则用带 cp 的同步图（同一 saver）
    graph = get_graph(hitl=bool(settings.hitl_enabled), settings=settings)
    config = {"configurable": {"thread_id": thread_id}}
    rows: List[Dict[str, Any]] = []
    for i, snap in enumerate(graph.get_state_history(config)):
        if i >= limit:
            break
        cfg = snap.config.get("configurable") or {}
        parent = (snap.parent_config or {}).get("configurable") or {}
        meta = snap.metadata or {}
        rows.append(
            {
                "checkpoint_id": cfg.get("checkpoint_id"),
                "parent_checkpoint_id": parent.get("checkpoint_id"),
                "next": list(snap.next or ()),
                "created_at": meta.get("created_at") or meta.get("ts"),
                "source": meta.get("source"),
                "step": meta.get("step"),
            }
        )
    return {
        "status": "ok",
        "thread_id": thread_id,
        "count": len(rows),
        "checkpoints": rows,
    }


def rollback_check(
    thread_id: str,
    checkpoint_id: str,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """从历史 checkpoint_id 时间旅行分叉续跑（不删旧历史）。"""
    settings = settings or get_settings()
    if not settings.checkpoint_sqlite_path:
        return {
            "status": "error",
            "error": "DD_CHECK_CHECKPOINT_SQLITE_PATH not set; cannot rollback",
            "thread_id": thread_id,
        }
    if not checkpoint_id:
        return {
            "status": "error",
            "error": "checkpoint_id is required",
            "thread_id": thread_id,
        }
    graph = get_graph(hitl=bool(settings.hitl_enabled), settings=settings)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
        }
    }
    # None input = continue from that checkpoint (time-travel fork)
    out = graph.invoke(None, config)
    return _format_invoke_out(out, thread_id=thread_id)


def invoke_batch(req: BatchCheckRequest, settings: Optional[Settings] = None) -> dict:
    """批量检查：逐份同步 invoke（批量不做 HITL 等待）。"""
    settings = settings or get_settings()
    note_hitl = ""
    if settings.hitl_enabled:
        note_hitl = "；批量模式已跳过 HITL（请单份 run_dd_check + resume）"
        settings = Settings(
            **{
                **_settings_dict(settings),
                "hitl_enabled": False,
            }
        )
    results: List[CheckResult] = []
    for item in req.items:
        if not item.phase or item.phase == "CHECK":
            item = item.model_copy(update={"phase": req.phase or "RECHECK"})
        results.append(invoke_check(item, settings))
    fail_dims: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            if f.status == FindingStatus.FAIL:
                fail_dims[f.dimension] = fail_dims.get(f.dimension, 0) + 1
    top = sorted(fail_dims.items(), key=lambda x: x[1], reverse=True)[:10]
    narrative = (
        f"共检查 {len(results)} 份报告。"
        + (
            "高频失败维度：" + "；".join(f"{k}×{v}" for k, v in top)
            if top
            else "未统计到失败维度。"
        )
    )
    return {
        "count": len(results),
        "results": [r.model_dump() for r in results],
        "aggregate_summary": narrative,
        "llm_summary": None,
        "graph": describe_graph(hitl_enabled=False),
        "note": (
            "摘要由规则聚合；配置 DD_CHECK_LLM_* 后将走 llm_summarize 节点"
            + note_hitl
        ),
    }
