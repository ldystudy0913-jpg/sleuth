"""答复框架生成流水线。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .attachments import AttachmentBundle, load_attachments
from .config import Settings, get_settings
from .kb import (
    KnowledgeBase,
    RiskPoint,
    load_kb_lexicon_only,
)
from .kb.remote import RiskRetrieval, retrieve_risk_codes
from .lexicon_guard import guard_and_rewrite, hard_rules_prompt_block
from .llm import LlmError, mockable_generate
from .models import DISCLAIMER, FrameworkRequest, FrameworkResult, VerificationItem

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "framework.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        "你是尽调答复框架生成助手。输出四段：预分析、答复正文框架、"
        "待核实清单、结论判定指引。供人工参考，最终判定由人工作出。"
    )


def _remote_risk_context(
    retrievals: List[RiskRetrieval],
) -> Tuple[str, List[str], List[str], List[Dict[str, Any]], List[Dict[str, str]]]:
    """Build prompt block + found/missing codes + per-code meta + source cites."""
    found: List[str] = []
    missing: List[str] = []
    meta_rows: List[Dict[str, Any]] = []
    blocks: List[str] = []
    sources: List[Dict[str, str]] = []
    seen_cite: set[str] = set()

    for r in retrievals:
        row: Dict[str, Any] = {
            "code": r.code,
            "source": r.source,
            "question": r.question,
            "hit_count": len(r.hits),
            "error": r.error,
        }
        if r.ok:
            found.append(r.code)
            cites = []
            for h in r.hits:
                cite = h.source_cite()
                cites.append(cite)
                key = cite
                if key not in seen_cite:
                    seen_cite.add(key)
                    sources.append(
                        {
                            "code": r.code,
                            "cite": cite,
                            "file_name": h.file_name,
                            "title": h.title,
                            "url": h.source_url(),
                            "knowledge_id": h.knowledge_id,
                        }
                    )
            hit_text = "\n".join(h.text_for_prompt() for h in r.hits)
            blocks.append(
                f"### 风险点 {r.code}（远程检索，question={r.question!r}）\n"
                f"请从下列知识摘录中归纳：风险点名称、对应尽调问题、回答判断要点、"
                f"对应材料、相关制度要点。材料清单用于判断附件是否充分，"
                f"不要求每份材料都必须齐套。引用知识时带上来源（文件名/链接）。\n"
                f"{hit_text}"
            )
            row["top_titles"] = [h.title or h.file_name for h in r.hits]
            row["sources"] = cites
        else:
            missing.append(r.code)
            reason = r.error or "empty_hits"
            blocks.append(
                f"### 风险点 {r.code}（检索失败或无结果：{reason}）\n"
                "请在正文中留【待核实】槽位，提示需人工查询知识库或补充制度。"
                "不要使用本地种子知识或臆造制度条文。"
            )
        meta_rows.append(row)

    text = "\n\n".join(blocks) if blocks else "（无风险点检索结果）"
    return text, found, missing, meta_rows, sources


def _attachment_context(bundle: AttachmentBundle) -> str:
    parts: List[str] = []
    for ex in bundle.excerpts:
        flag = "（已截断）" if ex.truncated else ""
        parts.append(f"### 附件 {ex.source}{flag}\n{ex.text}")
    if bundle.skipped:
        parts.append("### 跳过的附件\n" + "\n".join(f"- {s}" for s in bundle.skipped))
    return "\n\n".join(parts) if parts else "（无附件文本）"


def _fallback_framework(
    req: FrameworkRequest,
    found: List[RiskPoint],
    missing: List[str],
    bundle: AttachmentBundle,
    *,
    remote_blocks: str = "",
) -> str:
    """无 LLM 时的确定性骨架（便于测试与离线演示）。"""
    fields = req.fields_dict()
    filled = {k: v for k, v in fields.items() if str(v).strip()}
    empty = [k for k, v in fields.items() if not str(v).strip()]

    pre_lines = ["基于系统字段的可确定信息："]
    if filled:
        for k, v in filled.items():
            pre_lines.append(f"- {k}：{v}")
    else:
        pre_lines.append("- （10 个字段均未提供）")
    if empty:
        pre_lines.append("本步无法判断：" + "、".join(empty) + " 未提供，不得臆造。")
    if bundle.excerpts:
        pre_lines.append(
            f"已加载本地/远程附件摘要 {len(bundle.excerpts)} 份；"
            "请对照知识中的材料要求判断是否足以佐证，不必要求材料清单全部齐套。"
        )

    slot_n = 0
    body_parts: List[str] = []
    verify_parts: List[str] = []
    guide_parts: List[str] = []

    def next_slot(desc: str, methods: List[str], fmt: str, code: str) -> str:
        nonlocal slot_n
        slot_n += 1
        sid = f"待核实{slot_n}"
        body_parts.append(f"【{sid}：____】")
        verify_parts.append(
            f"- 【{sid}】（{code}）需了解：{desc}；建议方式：{'/'.join(methods)}；填写格式：{fmt}"
        )
        return sid

    for rp in found:
        body_parts.append(f"### {rp.code} {rp.name}（{rp.category}）")
        for q in rp.questions:
            body_parts.append(f"**问题**：{q}")
        body_parts.append(f"**答复要点**：{rp.answer_logic}")
        if rp.materials:
            body_parts.append(
                "**对应材料（充分性参考，非齐套强制）**：" + "、".join(rp.materials)
            )
        next_slot(
            f"按答复要点核实「{rp.name}」相关事实，并判断现有附件是否足以佐证",
            ["访谈", "调取材料", "系统查询"],
            "简述事实+材料名称+日期+是否充分",
            rp.code,
        )
        hints = rp.conclusion_hints or {}
        guide_parts.append(f"### {rp.code}")
        for label in ("可排除", "可缓释", "无法排除"):
            if label in hints:
                guide_parts.append(f"- {label}：{hints[label]}")

    for code in missing:
        body_parts.append(f"### {code}（知识库未覆盖）")
        next_slot(
            f"编码 {code} 检索无结果，需业务补充问题与材料要求",
            ["系统查询", "调取材料"],
            "补充知识条目或人工问题清单",
            code,
        )
        guide_parts.append(
            f"### {code}\n- 可排除/可缓释/无法排除：待知识库补充后由人工判定。"
        )

    if remote_blocks and not found and not missing:
        body_parts.append(remote_blocks)

    md = "\n".join(
        [
            "## 1. 预分析",
            "\n".join(pre_lines),
            "",
            "## 2. 答复正文框架",
            "\n".join(body_parts) if body_parts else "（无风险点）",
            "",
            "## 3. 待核实清单",
            "\n".join(verify_parts) if verify_parts else "- （无待核实项）",
            "",
            "## 4. 结论判定指引",
            "\n".join(guide_parts) if guide_parts else "（无）",
            "",
            f"> {DISCLAIMER}",
        ]
    )
    return md


_SECTION_RE = re.compile(
    r"##\s*1\.\s*预分析\s*(?P<pre>.*?)"
    r"##\s*2\.\s*答复正文框架\s*(?P<body>.*?)"
    r"##\s*3\.\s*待核实清单\s*(?P<ver>.*?)"
    r"##\s*4\.\s*结论判定指引\s*(?P<guide>.*)",
    re.DOTALL,
)


def _parse_sections(markdown: str) -> Dict[str, str]:
    m = _SECTION_RE.search(markdown)
    if not m:
        return {
            "pre_analysis": markdown.strip(),
            "reply_body": "",
            "verification_raw": "",
            "conclusion_guide": "",
        }
    return {
        "pre_analysis": m.group("pre").strip(),
        "reply_body": m.group("body").strip(),
        "verification_raw": m.group("ver").strip(),
        "conclusion_guide": m.group("guide").strip(),
    }


def _parse_verification_list(raw: str) -> List[VerificationItem]:
    items: List[VerificationItem] = []
    for line in (raw or "").splitlines():
        s = line.strip().lstrip("-").strip()
        if "待核实" not in s:
            continue
        m = re.search(r"待核实\d+", s)
        if not m:
            continue
        slot_id = m.group(0)
        code_m = re.search(r"\(([A-Z]\d{3})\)", s)
        code = code_m.group(1) if code_m else ""
        methods: List[str] = []
        for label in ("访谈", "实地", "调取材料", "系统查询"):
            if label in s:
                methods.append(label)
        items.append(
            VerificationItem(
                slot_id=slot_id,
                need_to_know=s,
                methods=methods or ["访谈", "调取材料"],
                fill_format="简述事实+材料名称+日期",
                related_risk_code=code,
            )
        )
    return items


def _sources_markdown(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    lines = ["## 知识来源", "以下条目来自知识库检索，供核对原文："]
    for i, s in enumerate(sources, start=1):
        code = s.get("code") or ""
        cite = s.get("cite") or s.get("file_name") or "未知来源"
        prefix = f"（{code}）" if code else ""
        lines.append(f"{i}. {prefix}{cite}")
    return "\n".join(lines)


def _resolve_knowledge(
    req: FrameworkRequest,
    settings: Settings,
    kb: KnowledgeBase,
) -> Tuple[str, List[RiskPoint], List[str], Dict[str, Any]]:
    """Return (prompt_context, structured_found_for_fallback, missing, kb_meta)."""
    del kb  # lexicon-only; risk points always come from the KB API
    if not settings.kb_api_configured():
        raise ValueError(
            "DD_REPLY_KB_API_URL is required; risk-point knowledge is remote-only"
        )

    meta: Dict[str, Any] = {
        "mode": "remote",
        "top_k": int(getattr(settings, "kb_top_k", 8) or 0),
    }
    retrievals = retrieve_risk_codes(req.risk_queries(), settings)
    ctx, found_codes, missing, rows, sources = _remote_risk_context(retrievals)
    meta["retrievals"] = rows
    meta["sources"] = sources
    found_rp: List[RiskPoint] = []
    for c in found_codes:
        ret = next((x for x in retrievals if x.code == c), None)
        title = ""
        para = ""
        if ret and ret.hits:
            title = ret.hits[0].title or ret.hits[0].file_name
            para = ret.hits[0].paragraph[:800]
        found_rp.append(
            RiskPoint(
                code=c,
                name=title or c,
                category="远程知识",
                questions=[f"请结合知识库摘录核实风险点 {c}"],
                answer_logic=para or "结合检索摘录组织核实要点；判断附件是否足以佐证。",
                materials=[],
                conclusion_hints={},
            )
        )
    return ctx, found_rp, missing, meta


def generate_framework(
    req: FrameworkRequest,
    *,
    settings: Optional[Settings] = None,
    kb: Optional[KnowledgeBase] = None,
    mock_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    use_llm: bool = True,
) -> FrameworkResult:
    settings = settings or get_settings()
    if not req.risk_queries():
        raise ValueError("provide at least one risk code or risk name")
    if not settings.kb_api_configured():
        raise ValueError(
            "DD_REPLY_KB_API_URL is required; risk-point knowledge is remote-only"
        )

    if kb is None:
        kb = load_kb_lexicon_only(settings.kb_path)

    risk_ctx, found, missing, kb_meta = _resolve_knowledge(req, settings, kb)
    bundle = load_attachments(
        local_paths=req.local_paths,
        invest_id=req.invest_id,
        settings=settings,
    )

    markdown = ""
    llm_used = False
    llm_error = ""
    materials_note = (
        "\n\n【材料与附件】知识中的「对应材料」是充分性参考清单："
        "请判断现有附件是否已能佐证与分析问题；不要求清单内每项材料都必须存在。"
    )
    if use_llm and (settings.llm_configured() or mock_llm is not None):
        system = _load_system_prompt() + "\n\n" + hard_rules_prompt_block(kb)
        user = (
            "【系统字段】\n"
            + json.dumps(req.fields_dict(), ensure_ascii=False, indent=2)
            + "\n\n【风险点知识】\n"
            + risk_ctx
            + materials_note
            + "\n\n【附件摘要】\n"
            + _attachment_context(bundle)
            + "\n\n请生成四段式答复框架。引用知识摘录时必须写明来源（文件名、链接或 knowledgeId）。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            markdown = mockable_generate(messages, settings, mock_fn=mock_llm)
            llm_used = True
        except LlmError as exc:
            llm_error = str(exc)
            markdown = _fallback_framework(req, found, missing, bundle)
    else:
        markdown = _fallback_framework(req, found, missing, bundle)

    guarded = guard_and_rewrite(markdown, kb, rewrite_hard=True)
    markdown = guarded.text
    if (
        llm_used
        and guarded.hard_hits
        and mock_llm is None
        and settings.llm_configured()
    ):
        try:
            retry_msgs = [
                {
                    "role": "system",
                    "content": _load_system_prompt()
                    + "\n\n"
                    + hard_rules_prompt_block(kb)
                    + "\n上一稿含禁用词，请全文重写并避免这些表述。",
                },
                {"role": "user", "content": markdown},
            ]
            markdown2 = mockable_generate(retry_msgs, settings)
            guarded2 = guard_and_rewrite(markdown2, kb, rewrite_hard=True)
            markdown = guarded2.text
            guarded = guarded2
        except LlmError:
            pass

    if DISCLAIMER not in markdown:
        markdown = markdown.rstrip() + f"\n\n> {DISCLAIMER}\n"
    src_md = _sources_markdown(list(kb_meta.get("sources") or []))
    if src_md and "知识来源" not in markdown:
        markdown = markdown.rstrip() + "\n\n" + src_md + "\n"

    sections = _parse_sections(markdown)
    verification = _parse_verification_list(sections.get("verification_raw", ""))
    soft_warnings = [
        {
            "rule_id": h.rule_id,
            "category": h.category,
            "pattern": h.pattern,
            "match_mode": h.match_mode,
            "matched_text": h.matched_text,
            "alternatives": h.alternatives,
        }
        for h in guarded.soft_hits
    ]
    blocked = [
        {
            "rule_id": h.rule_id,
            "category": h.category,
            "pattern": h.pattern,
            "match_mode": h.match_mode,
            "matched_text": h.matched_text,
            "alternatives": h.alternatives,
        }
        for h in guarded.hard_hits
    ]

    meta: Dict[str, Any] = {
        "found_codes": [r.code for r in found],
        "missing_codes": missing,
        "kb": kb_meta,
        "attachment_count": len(bundle.excerpts),
        "attachment_skipped": list(bundle.skipped),
        "llm_used": llm_used,
        "llm_error": llm_error,
        "soft_warnings": soft_warnings,
        "blocked_phrases": blocked,
        "lexicon_rewritten": guarded.rewritten,
        "disclaimer": DISCLAIMER,
        "report_id": req.report_id,
        "bank_id": req.bank_id,
    }
    return FrameworkResult(
        pre_analysis=sections.get("pre_analysis", ""),
        reply_body=sections.get("reply_body", ""),
        verification_list=verification,
        conclusion_guide=sections.get("conclusion_guide", ""),
        markdown=markdown,
        meta=meta,
    )
