"""Due-diligence report check. MCP handlers call this; keep I/O out of mcp_server.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .attachments import load_excerpts, summarize_refs
from .config import Settings
from .kb import search as kb_search
from .llm import LlmError, LlmFn, complete_json, settings_with_llm_json
from .output import emit_file
from .rubric import (
    RubricError,
    aggregate_score,
    dimension_ids,
    kb_max_queries,
    load_rubric,
    rubric_guidance,
    seed_queries,
)
from .report_docx import render_docx_bytes


def _http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _pretty_json(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(data, ensure_ascii=False, indent=2)


def _read_text(path) -> str:
    return path.read_text(encoding="utf-8")


def _fill_prompt(template: str, mapping: Dict[str, str]) -> str:
    out = template
    for key, val in mapping.items():
        out = out.replace("{" + key + "}", val)
    return out


def _merge_sources(*groups: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not _http_url(url):
                continue
            key = url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            title = str(item.get("title") or item.get("file_name") or item.get("name") or key)
            out.append({"title": title, "url": url})
    return out


def _run_kb_queries(
    questions: List[str],
    settings: Settings,
    *,
    cap: int,
    opener=None,
) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen_q = set()
    count = 0
    for q in questions:
        qn = (q or "").strip()
        if not qn or qn in seen_q:
            continue
        seen_q.add(qn)
        if cap > 0 and count >= cap:
            break
        count += 1
        body = kb_search(qn, settings, opener=opener)
        sources.extend(body.get("sources") or [])
    return sources


def _normalize_findings(raw: Any, allowed: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    allowed_set = set(allowed)
    for item in raw:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "").strip()
        if dim and dim not in allowed_set:
            continue
        out.append(
            {
                "dimension": dim,
                "severity": str(item.get("severity") or "info").strip() or "info",
                "location": str(item.get("location") or "").strip(),
                "issue": str(item.get("issue") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return out


def _word_filename(settings: Settings, rubric: Dict[str, Any], score: float) -> str:
    word = rubric.get("word") if isinstance(rubric.get("word"), dict) else {}
    pattern = settings.word_filename or str(word.get("filename") or "")
    date_fmt = str(word.get("date_format") or "")
    date_s = datetime.now(timezone.utc).strftime(date_fmt)
    score_s = str(score)
    return pattern.replace("{score}", score_s).replace("{date}", date_s)


def check_report(
    settings: Settings,
    *,
    report_text: str = "",
    report_json: str = "",
    question: str = "",
    attachment_refs: Optional[List[dict]] = None,
    sleuth_llm_json: str = "",
    llm_fn: Optional[LlmFn] = None,
    kb_opener=None,
    emit_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    empty = {"ok": False, "score": None, "findings": [], "sources": [], "files": []}
    settings = settings_with_llm_json(settings, sleuth_llm_json)
    try:
        rubric = load_rubric(settings.rubric_path)
    except RubricError as exc:
        return {**empty, "detail": str(exc)}
    if not settings.llm_configured() and llm_fn is None:
        return {
            **empty,
            "detail": (
                "LLM not configured: set DD_CHECK_LLM_BASE_URL, DD_CHECK_LLM_API_KEY, "
                "DD_CHECK_LLM_MODEL (or leave them empty and call via Sleuth)"
            ),
        }
    try:
        system_t = _read_text(settings.system_prompt_path)
        user_t = _read_text(settings.user_prompt_path)
    except OSError as exc:
        return {**empty, "detail": f"prompt file missing: {exc}"}

    ids = dimension_ids(rubric)
    score_max = str((rubric.get("score") or {}).get("max"))
    att_summary = summarize_refs(attachment_refs or [])
    excerpts, skipped = load_excerpts(attachment_refs or [])
    att_block = json.dumps(
        {"excerpts": excerpts, "skipped": skipped, "summary": att_summary},
        ensure_ascii=False,
        indent=2,
    )
    report_json_pretty = _pretty_json(report_json)
    mapping_base = {
        "score_max": score_max,
        "dimension_ids": ", ".join(ids),
        "question": (question or "").strip() or "请检查该尽调报告填写是否有问题。",
        "rubric_guidance": rubric_guidance(rubric),
        "report_text": (report_text or "").strip() or "(无)",
        "report_json": report_json_pretty or "(无)",
        "attachments": att_block,
    }

    sources: List[Dict[str, Any]] = []
    cap = kb_max_queries(rubric, int(settings.kb_max_queries or 0))
    parsed: Dict[str, Any] = {}

    def _ask(kb_block: str) -> Dict[str, Any]:
        system = _fill_prompt(system_t, mapping_base)
        user = _fill_prompt(user_t, {**mapping_base, "kb": kb_block or "(无)"})
        return complete_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            settings,
            llm_fn=llm_fn,
        )

    try:
        if settings.kb_enabled:
            seed = seed_queries(rubric)
            sources.extend(_run_kb_queries(seed, settings, cap=cap, opener=kb_opener))
            parsed = _ask(json.dumps(sources, ensure_ascii=False, indent=2) if sources else "(无)")
            extra = parsed.get("kb_questions") if isinstance(parsed.get("kb_questions"), list) else []
            extra_q = [str(x) for x in extra if str(x).strip()]
            remain = cap - len(seed) if cap > 0 else cap
            if extra_q and (remain > 0 or cap <= 0):
                more = _run_kb_queries(
                    extra_q,
                    settings,
                    cap=remain if cap > 0 else cap,
                    opener=kb_opener,
                )
                if more:
                    sources = _merge_sources(sources, more)
                    parsed = _ask(json.dumps(sources, ensure_ascii=False, indent=2))
        else:
            parsed = _ask("(未启用知识库)")
    except LlmError as exc:
        return {**empty, "detail": str(exc)}

    scores_raw = parsed.get("dimension_scores") if isinstance(parsed.get("dimension_scores"), dict) else {}
    score = aggregate_score(scores_raw, rubric)
    findings = _normalize_findings(parsed.get("findings"), ids)
    summary = str(parsed.get("summary") or "").strip()
    sources = _merge_sources(sources, parsed.get("sources") if isinstance(parsed.get("sources"), list) else [])

    files: List[Dict[str, Any]] = []
    word_detail = ""
    try:
        docx_bytes = render_docx_bytes(
            rubric=rubric,
            score=score,
            summary=summary,
            findings=findings,
            sources=sources,
        )
        mime = str((rubric.get("word") or {}).get("mime") or "")
        filename = _word_filename(settings, rubric, score)
        uploader = emit_fn or emit_file
        uploaded = uploader(
            settings,
            filename=filename,
            content_bytes=docx_bytes,
            mime=mime,
        )
        if uploaded.get("ok"):
            files = list(uploaded.get("files") or [])
        else:
            word_detail = str(uploaded.get("detail") or "word packaging failed")
    except Exception as exc:
        word_detail = f"word render/upload failed: {exc}"

    body: Dict[str, Any] = {
        "ok": True,
        "score": score,
        "summary": summary,
        "findings": findings,
        "dimension_scores": {k: scores_raw[k] for k in scores_raw},
        "sources": sources,
        "files": files,
        "attachment_skipped": skipped,
    }
    if word_detail:
        body["word_detail"] = word_detail
    return body
