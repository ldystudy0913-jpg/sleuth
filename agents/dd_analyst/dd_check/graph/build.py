"""组装尽调检查 LangGraph：节点顺序与条件边。

主流程（同步，HITL 关）:
  ingest → strategy → parse → [附件?] → rules → score → [llm?] → emit → END

HITL 开时在 score/(llm) 之后插入 human_confirm（interrupt），再 emit。
checkpointer 由调用方注入（持久 SQLite 或测试 MemorySaver）。
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from . import nodes
from .checkpoint import PickleSerde, _PickleSerde  # noqa: F401 — re-export
from .routing import after_parse, after_score, after_summary
from .state import CheckState


def build_check_graph(*, hitl: bool = False, checkpointer: Any = None) -> Any:
    """编译检查图。

    hitl：是否启用 human_confirm 路由语义由 state.hitl_enabled 决定；
    本参数仅表示「需要 checkpointer 才能 interrupt/resume」时调用方应传入 saver。
    若传入 checkpointer，则 compile 时挂上（同步路径也可挂以落节点 checkpoint）。
    """
    g: StateGraph = StateGraph(CheckState)
    g.add_node("ingest_normalize", nodes.ingest_normalize)
    g.add_node("resolve_strategy", nodes.resolve_strategy)
    g.add_node("parse_report", nodes.parse_report)
    g.add_node("fetch_attachments", nodes.fetch_attachments)
    g.add_node("skip_attachments", nodes.skip_attachments)
    g.add_node("run_rule_dims", nodes.run_rule_dims)
    g.add_node("score_aggregate", nodes.score_aggregate)
    g.add_node("llm_summarize", nodes.llm_summarize)
    g.add_node("human_confirm", nodes.human_confirm)
    g.add_node("emit_result", nodes.emit_result)

    g.add_edge(START, "ingest_normalize")
    g.add_edge("ingest_normalize", "resolve_strategy")
    g.add_edge("resolve_strategy", "parse_report")
    g.add_conditional_edges(
        "parse_report",
        after_parse,
        {
            "fetch_attachments": "fetch_attachments",
            "skip_attachments": "skip_attachments",
        },
    )
    g.add_edge("fetch_attachments", "run_rule_dims")
    g.add_edge("skip_attachments", "run_rule_dims")
    g.add_edge("run_rule_dims", "score_aggregate")
    g.add_conditional_edges(
        "score_aggregate",
        after_score,
        {
            "llm_summarize": "llm_summarize",
            "human_confirm": "human_confirm",
            "emit_result": "emit_result",
        },
    )
    g.add_conditional_edges(
        "llm_summarize",
        after_summary,
        {
            "human_confirm": "human_confirm",
            "emit_result": "emit_result",
        },
    )
    g.add_edge("human_confirm", "emit_result")
    g.add_edge("emit_result", END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    # HITL 图必须有 checkpointer；无调用方注入时仍要求显式传入（runner 负责）
    if hitl:
        raise RuntimeError(
            "HITL graph requires a checkpointer; set DD_CHECK_CHECKPOINT_SQLITE_PATH "
            "and apply deploy/ddl_langgraph_checkpoint.sql"
        )
    return g.compile()


def describe_graph(hitl_enabled: Optional[bool] = None) -> dict:
    """给 MCP describe_graph / 运维看的静态图说明（不执行）。"""
    return {
        "name": "dd_analyst_check",
        "hitl_enabled": hitl_enabled,
        "nodes": [
            "ingest_normalize",
            "resolve_strategy",
            "parse_report",
            "fetch_attachments|skip_attachments",
            "run_rule_dims",
            "score_aggregate",
            "llm_summarize?",
            "human_confirm?",
            "emit_result",
        ],
        "edges": [
            "START → ingest_normalize → resolve_strategy → parse_report",
            "parse_report → (need_attachments) fetch_attachments | skip_attachments → run_rule_dims",
            "run_rule_dims → score_aggregate → (llm?) → (HITL?) human_confirm | emit_result → END",
            "human_confirm (interrupt) → emit_result",
            "rollback: list_dd_checkpoints + rollback_dd_check (time-travel fork)",
        ],
    }
