"""打分汇总：findings → 维度分 → 总分/等级/摘要。

节点 score_aggregate 调用 aggregate_score(...)。
PASS=1.0 / WARN=0.6 / FAIL=0 / SKIP 不计入权重。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from ..config import Settings
from ..models import DimensionScore, Finding, FindingStatus


def aggregate_score(
    findings: List[Finding],
    enabled_dimensions: List[str],
    settings: Settings,
) -> Tuple[float, str, List[DimensionScore], str]:
    """按启用维度加权汇总，返回 (score, grade, dim_scores, summary)。"""
    by_dim: Dict[str, List[Finding]] = defaultdict(list)
    for f in findings:
        by_dim[f.dimension].append(f)

    dim_scores: List[DimensionScore] = []
    total_w = 0.0
    earned = 0.0
    for dim in enabled_dimensions:
        items = by_dim.get(dim) or []
        weight = float(settings.score_weights.get(dim, 1.0))
        status = _dim_status(items)
        if status == FindingStatus.SKIP:
            dim_scores.append(DimensionScore(dimension=dim, status=status, weight=weight, contribution=0.0))
            continue
        factor = {FindingStatus.PASS: 1.0, FindingStatus.WARN: 0.6, FindingStatus.FAIL: 0.0}[status]
        contrib = weight * factor
        total_w += weight
        earned += contrib
        dim_scores.append(DimensionScore(dimension=dim, status=status, weight=weight, contribution=contrib))

    score = 100.0 if total_w <= 0 else round(100.0 * earned / total_w, 2)
    grade = _grade(score, dim_scores)
    summary = _summary(score, grade, findings)
    return score, grade, dim_scores, summary


def _dim_status(items: List[Finding]) -> FindingStatus:
    """单维度状态：有 FAIL 则 FAIL，否则 WARN，否则 PASS；无 finding 为 SKIP。"""
    if not items:
        return FindingStatus.SKIP
    if any(i.status == FindingStatus.FAIL for i in items):
        return FindingStatus.FAIL
    if any(i.status == FindingStatus.WARN for i in items):
        return FindingStatus.WARN
    if all(i.status == FindingStatus.SKIP for i in items):
        return FindingStatus.SKIP
    return FindingStatus.PASS


def _grade(score: float, dim_scores: List[DimensionScore]) -> str:
    """有 FAIL 时等级封顶偏严；否则按分数段 A–E。"""
    if any(d.status == FindingStatus.FAIL for d in dim_scores):
        if score >= 80:
            return "C"
        if score >= 60:
            return "D"
        return "E"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "E"


def _summary(score: float, grade: str, findings: List[Finding]) -> str:
    """中文一句话摘要（规则侧，非 LLM）。"""
    fails = [f for f in findings if f.status == FindingStatus.FAIL]
    warns = [f for f in findings if f.status == FindingStatus.WARN]
    parts = [f"综合得分 {score}（等级 {grade}）"]
    if fails:
        parts.append(f"致命/失败项 {len(fails)} 条")
    if warns:
        parts.append(f"警告项 {len(warns)} 条")
    if not fails and not warns:
        parts.append("未发现明显问题")
    return "；".join(parts)
