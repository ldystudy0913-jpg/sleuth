"""LangGraph 检查状态：各节点读写的共享字段。

典型流转：request/settings → cust/phase/strategy → facts → attachments
→ findings → score/grade/summary → [human_*] → result。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from ..models import CheckRequest, Finding, ReportFacts
from ..strategy import Strategy


class CheckState(TypedDict, total=False):
    # —— 入参 ——
    request: CheckRequest  # 单份检查请求（报告字段）
    settings: Any  # DD_CHECK_* 配置

    # —— 归一化 / 策略 ——
    cust_type: str
    phase: str
    strategy: Optional[Strategy]
    enabled_dimensions: List[str]
    need_attachments: bool  # 是否走 fetch_attachments

    # —— 中间产物 ——
    facts: Optional[ReportFacts]  # 表单解析结果
    attachments: Any  # AttachmentBundle
    skipped_attachments: List[str]
    findings: List[Finding]

    # —— 打分与摘要 ——
    score: float
    grade: str
    dimension_scores: List[Any]
    summary: str
    llm_enabled: bool

    # —— HITL ——
    hitl_enabled: bool
    hitl_on_fail_only: bool
    human_decision: Optional[Dict[str, Any]]
    human_status: str

    # —— 输出 / 审计 ——
    result: Optional[Dict[str, Any]]  # emit_result 写出的 CheckResult 字典
    trace: List[Dict[str, Any]]  # 各节点耗时
    errors: List[str]
