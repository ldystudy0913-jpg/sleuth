"""从知识库 lexicon 扫描输出：hard / soft（子串 + 正则）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Pattern

from .kb import KnowledgeBase, LexiconRule, load_kb


@dataclass
class LexiconHit:
    rule_id: int
    category: str
    level: str
    pattern: str
    alternatives: List[str] = field(default_factory=list)
    match_mode: str = "substring"  # substring | regex
    matched_text: str = ""


@dataclass
class GuardResult:
    text: str
    hard_hits: List[LexiconHit] = field(default_factory=list)
    soft_hits: List[LexiconHit] = field(default_factory=list)
    rewritten: bool = False


_REGEX_CACHE: dict[str, Pattern[str]] = {}


def _compile_regex(expr: str) -> Optional[Pattern[str]]:
    cached = _REGEX_CACHE.get(expr)
    if cached is not None:
        return cached
    try:
        compiled = re.compile(expr, re.IGNORECASE)
    except re.error:
        return None
    _REGEX_CACHE[expr] = compiled
    return compiled


def _find_hits(text: str, rules: List[LexiconRule]) -> List[LexiconHit]:
    hits: List[LexiconHit] = []
    for rule in rules:
        for pat in rule.banned_patterns:
            if not pat:
                continue
            if pat in text:
                hits.append(
                    LexiconHit(
                        rule_id=rule.id,
                        category=rule.category,
                        level=rule.level,
                        pattern=pat,
                        alternatives=list(rule.alternatives),
                        match_mode="substring",
                        matched_text=pat,
                    )
                )
        for expr in rule.banned_regex:
            if not expr:
                continue
            compiled = _compile_regex(expr)
            if compiled is None:
                continue
            m = compiled.search(text)
            if not m:
                continue
            hits.append(
                LexiconHit(
                    rule_id=rule.id,
                    category=rule.category,
                    level=rule.level,
                    pattern=expr,
                    alternatives=list(rule.alternatives),
                    match_mode="regex",
                    matched_text=m.group(0),
                )
            )
    return hits


def scan_text(text: str, kb: Optional[KnowledgeBase] = None) -> GuardResult:
    kb = kb or load_kb()
    hard = _find_hits(text, kb.hard_rules())
    soft = _find_hits(text, kb.soft_rules())
    return GuardResult(text=text, hard_hits=hard, soft_hits=soft)


def guard_and_rewrite(
    text: str,
    kb: Optional[KnowledgeBase] = None,
    *,
    rewrite_hard: bool = True,
) -> GuardResult:
    """扫描；hard 子串命中则替换为首选推荐表述；正则命中仅标注（避免盲目整句替换）。"""
    kb = kb or load_kb()
    first = scan_text(text, kb)
    if not first.hard_hits or not rewrite_hard:
        return first
    rewritten = text
    for hit in first.hard_hits:
        if hit.match_mode != "substring":
            continue
        if hit.alternatives and hit.pattern in rewritten:
            rewritten = rewritten.replace(hit.pattern, hit.alternatives[0])
    second = scan_text(rewritten, kb)
    second.rewritten = rewritten != text
    second.text = rewritten
    return second


def hard_rules_prompt_block(kb: Optional[KnowledgeBase] = None, *, limit: int = 40) -> str:
    """注入 hard 规则的类别意图（示例非穷尽）。"""
    kb = kb or load_kb()
    lines: List[str] = [
        "【禁用表述（知识库硬拦截）】",
        "以下为类别意图：示例词非穷尽，凡落入意图范围的表述均禁止。",
    ]
    for rule in kb.hard_rules()[:limit]:
        intent = rule.intent or "（见禁止示例）"
        samples = "、".join((rule.examples or rule.banned_patterns)[:3])
        alt = (
            "；".join(rule.alternatives[:2])
            if rule.alternatives
            else "（改用客观、可核实表述）"
        )
        lines.append(
            f"- [{rule.category}#{rule.id}] {intent} "
            f"示例（非穷尽）：{samples or '—'} → 推荐：{alt}"
        )
    lines.append("全部输出仅供尽调人员参考，最终判定由人工作出。")
    return "\n".join(lines)
