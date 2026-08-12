"""知识库：本地 lexicon +（可选）本地 risk_points 种子；生产风险点走 remote 检索。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .remote import (
    KbApiError,
    KbHit,
    RiskRetrieval,
    retrieve_risk_code,
    retrieve_risk_codes,
    search_knowledge,
)

_DEFAULT_KB_DIR = Path(__file__).resolve().parent


@dataclass
class RiskPoint:
    code: str
    name: str
    category: str = ""
    questions: List[str] = field(default_factory=list)
    answer_logic: str = ""
    materials: List[str] = field(default_factory=list)
    conclusion_hints: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RiskPoint":
        hints = d.get("conclusion_hints") or {}
        if not isinstance(hints, dict):
            hints = {}
        qs = d.get("questions") or []
        if isinstance(qs, str):
            qs = [qs]
        mats = d.get("materials") or []
        if isinstance(mats, str):
            mats = [mats]
        return cls(
            code=str(d.get("code") or "").strip().upper(),
            name=str(d.get("name") or "").strip(),
            category=str(d.get("category") or "").strip(),
            questions=[str(x).strip() for x in qs if str(x).strip()],
            answer_logic=str(d.get("answer_logic") or "").strip(),
            materials=[str(x).strip() for x in mats if str(x).strip()],
            conclusion_hints={str(k): str(v) for k, v in hints.items()},
        )


@dataclass
class LexiconRule:
    id: int
    category: str
    banned_patterns: List[str]
    alternatives: List[str]
    level: str  # hard | soft
    intent: str = ""
    banned_regex: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LexiconRule":
        pats = d.get("banned_patterns") or []
        alts = d.get("alternatives") or []
        regs = d.get("banned_regex") or []
        exs = d.get("examples") or []
        if isinstance(pats, str):
            pats = [pats]
        if isinstance(alts, str):
            alts = [alts]
        if isinstance(regs, str):
            regs = [regs]
        if isinstance(exs, str):
            exs = [exs]
        level = str(d.get("level") or "soft").strip().lower()
        if level not in {"hard", "soft"}:
            level = "soft"
        return cls(
            id=int(d.get("id") or 0),
            category=str(d.get("category") or "").strip(),
            intent=str(d.get("intent") or "").strip(),
            banned_patterns=[str(x) for x in pats if str(x).strip()],
            banned_regex=[str(x) for x in regs if str(x).strip()],
            examples=[str(x) for x in exs if str(x).strip()],
            alternatives=[str(x) for x in alts if str(x).strip()],
            level=level,
        )


@dataclass
class KnowledgeBase:
    risk_points: Dict[str, RiskPoint] = field(default_factory=dict)
    lexicon: List[LexiconRule] = field(default_factory=list)
    root: Optional[Path] = None

    def lookup_risks(self, codes: List[str]) -> Tuple[List[RiskPoint], List[str]]:
        found: List[RiskPoint] = []
        missing: List[str] = []
        seen: set[str] = set()
        for raw in codes:
            code = str(raw or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            item = self.risk_points.get(code)
            if item is None:
                missing.append(code)
            else:
                found.append(item)
        return found, missing

    def hard_rules(self) -> List[LexiconRule]:
        return [r for r in self.lexicon if r.level == "hard"]

    def soft_rules(self) -> List[LexiconRule]:
        return [r for r in self.lexicon if r.level == "soft"]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_kb_root(kb_path: Optional[Path] = None) -> Path:
    if kb_path is not None:
        return Path(kb_path)
    return _DEFAULT_KB_DIR


def load_lexicon(kb_path: Optional[Path] = None) -> List[LexiconRule]:
    """仅加载本地禁用词（生产与联调均使用）。"""
    root = resolve_kb_root(kb_path)
    lex_file = root / "lexicon.json"
    if not lex_file.is_file():
        raise FileNotFoundError(f"missing knowledge file: {lex_file}")
    lex_raw = _read_json(lex_file)
    lex_items = lex_raw.get("items") if isinstance(lex_raw, dict) else lex_raw
    if not isinstance(lex_items, list):
        raise ValueError("lexicon.json must contain an items array")
    return [LexiconRule.from_dict(x) for x in lex_items if isinstance(x, dict)]


def load_local_risk_points(kb_path: Optional[Path] = None) -> Dict[str, RiskPoint]:
    """加载本地 risk_points.json（离线种子 / 可选回退）。"""
    root = resolve_kb_root(kb_path)
    risk_file = root / "risk_points.json"
    if not risk_file.is_file():
        raise FileNotFoundError(f"missing knowledge file: {risk_file}")
    risk_raw = _read_json(risk_file)
    items = risk_raw.get("items") if isinstance(risk_raw, dict) else risk_raw
    if not isinstance(items, list):
        raise ValueError("risk_points.json must contain an items array")
    risk_map: Dict[str, RiskPoint] = {}
    for entry in items:
        if not isinstance(entry, dict):
            continue
        rp = RiskPoint.from_dict(entry)
        if rp.code:
            risk_map[rp.code] = rp
    return risk_map


def load_kb(kb_path: Optional[Path] = None) -> KnowledgeBase:
    """加载本地 risk_points.json + lexicon.json（无远程 URL 时的默认知识源）。"""
    root = resolve_kb_root(kb_path)
    return KnowledgeBase(
        risk_points=load_local_risk_points(kb_path),
        lexicon=load_lexicon(kb_path),
        root=root,
    )


def load_kb_lexicon_only(kb_path: Optional[Path] = None) -> KnowledgeBase:
    """仅 lexicon；风险点由远程检索填充时使用。"""
    root = resolve_kb_root(kb_path)
    return KnowledgeBase(risk_points={}, lexicon=load_lexicon(kb_path), root=root)


def list_risk_codes(kb: Optional[KnowledgeBase] = None) -> List[str]:
    kb = kb or load_kb()
    return sorted(kb.risk_points.keys())


def list_lexicon(kb: Optional[KnowledgeBase] = None) -> List[Dict[str, Any]]:
    kb = kb or load_kb()
    return [
        {
            "id": r.id,
            "category": r.category,
            "intent": r.intent,
            "banned_patterns": r.banned_patterns,
            "banned_regex": r.banned_regex,
            "examples": r.examples,
            "alternatives": r.alternatives,
            "level": r.level,
        }
        for r in kb.lexicon
    ]
