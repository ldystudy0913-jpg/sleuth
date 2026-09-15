"""Due-diligence report check. MCP handlers call this; keep I/O out of mcp_server.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .attachments import load_excerpts, summarize_refs
from .config import Settings
from .history import persist_check
from .kb import search as kb_search
from .llm import LlmError, LlmFn, complete_json, settings_with_llm_json
from .objects import WORD_CONTENT_TYPE
from .output import emit_file
from .report_docx import render_conflicts_docx_bytes, render_docx_bytes
from .rubric import (
    RubricError,
    aggregate_score,
    dimension_ids,
    kb_max_queries,
    load_rubric,
    rubric_guidance,
    seed_queries,
)
from .scenarios import ScenarioError, ScenarioPack, default_pack, get_pack


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


def _normalize_conflicts(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in {"obvious", "suspected"}:
            verdict = "suspected"
        out.append(
            {
                "item": str(item.get("item") or "").strip(),
                "info1": str(item.get("info1") or "").strip(),
                "info1_source": str(item.get("info1_source") or "").strip(),
                "info2": str(item.get("info2") or "").strip(),
                "info2_source": str(item.get("info2_source") or "").strip(),
                "verdict": verdict,
                "detail": str(item.get("detail") or "").strip(),
                "suggestion": str(item.get("suggestion") or "").strip(),
            }
        )
    return out


def _word_filename(settings: Settings, rubric: Dict[str, Any], score: Any) -> str:
    word = rubric.get("word") if isinstance(rubric.get("word"), dict) else {}
    pattern = settings.word_filename or str(word.get("filename") or "")
    date_fmt = str(word.get("date_format") or "")
    date_s = datetime.now(timezone.utc).strftime(date_fmt)
    score_s = "" if score is None else str(score)
    return pattern.replace("{score}", score_s).replace("{date}", date_s)


def _empty_fail(detail: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "score": None,
        "findings": [],
        "conflicts": [],
        "sources": [],
        "files": [],
        "detail": detail,
    }


def load_scenario(settings: Settings, scenario_id: str) -> ScenarioPack:
    sid = (scenario_id or "").strip() or "default"
    try:
        return get_pack(settings.config_dir, sid)
    except ScenarioError:
        return default_pack(settings.config_dir)


def _ask_llm(
    *,
    system_t: str,
    user_t: str,
    mapping_base: Dict[str, str],
    settings: Settings,
    llm_fn: Optional[LlmFn],
    kb_block: str,
) -> Dict[str, Any]:
    system = _fill_prompt(system_t, mapping_base)
    user = _fill_prompt(user_t, {**mapping_base, "kb": kb_block or "(none)"})
    return complete_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        settings,
        llm_fn=llm_fn,
    )


def _run_model(
    *,
    settings: Settings,
    rubric: Dict[str, Any],
    system_t: str,
    user_t: str,
    mapping_base: Dict[str, str],
    llm_fn: Optional[LlmFn],
    kb_opener,
    progress_fn: Optional[Callable[..., None]],
) -> tuple:
    def _progress(stage: str) -> None:
        if callable(progress_fn):
            try:
                progress_fn(stage)
            except Exception:
                pass

    sources: List[Dict[str, Any]] = []
    cap = kb_max_queries(rubric, int(settings.kb_max_queries or 0))

    def _ask(kb_block: str) -> Dict[str, Any]:
        return _ask_llm(
            system_t=system_t,
            user_t=user_t,
            mapping_base=mapping_base,
            settings=settings,
            llm_fn=llm_fn,
            kb_block=kb_block,
        )

    if settings.kb_enabled:
        _progress("kb")
        seed = seed_queries(rubric)
        sources.extend(_run_kb_queries(seed, settings, cap=cap, opener=kb_opener))
        _progress("llm")
        parsed = _ask(json.dumps(sources, ensure_ascii=False, indent=2) if sources else "(none)")
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
        _progress("llm")
        parsed = _ask("(kb disabled)")
    return parsed, sources


def _render_word(
    *,
    pack: ScenarioPack,
    rubric: Dict[str, Any],
    score: Any,
    summary: str,
    findings: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> bytes:
    if pack.output == "conflicts":
        return render_conflicts_docx_bytes(
            rubric=rubric,
            summary=summary,
            conflicts=conflicts,
            sources=sources,
            scenario_title=pack.title,
        )
    return render_docx_bytes(
        rubric=rubric,
        score=float(score or 0),
        summary=summary,
        findings=findings,
        sources=sources,
    )


def check_report(
    settings: Settings,
    *,
    report_text: str = "",
    report_json: str = "",
    question: str = "",
    attachment_refs: Optional[List[dict]] = None,
    sleuth_llm_json: str = "",
    scenario: str = "",
    report_id: str = "",
    llm_fn: Optional[LlmFn] = None,
    kb_opener=None,
    emit_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    progress_fn: Optional[Callable[..., None]] = None,
    persist_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    settings = settings_with_llm_json(settings, sleuth_llm_json)
    pack = load_scenario(settings, scenario)
    try:
        rubric = load_rubric(pack.rubric_path)
    except RubricError as exc:
        return _empty_fail(str(exc))
    if not settings.llm_configured() and llm_fn is None:
        return _empty_fail(
            "LLM not configured: set DD_CHECK_LLM_BASE_URL, DD_CHECK_LLM_API_KEY, "
            "DD_CHECK_LLM_MODEL (or leave them empty and call via Sleuth)"
        )
    try:
        system_t = _read_text(pack.system_path)
        user_t = _read_text(pack.user_path)
    except OSError as exc:
        return _empty_fail(f"prompt file missing: {exc}")

    def _progress(stage: str) -> None:
        if callable(progress_fn):
            try:
                progress_fn(stage)
            except Exception:
                pass

    _progress("normalize")
    ids = dimension_ids(rubric)
    score_cfg = rubric.get("score") if isinstance(rubric.get("score"), dict) else {}
    score_max = str(score_cfg.get("max") or "")
    att_summary = summarize_refs(attachment_refs or [])
    excerpts, skipped = load_excerpts(attachment_refs or [])
    att_block = json.dumps(
        {"excerpts": excerpts, "skipped": skipped, "summary": att_summary},
        ensure_ascii=False,
        indent=2,
    )
    mapping_base = {
        "score_max": score_max,
        "dimension_ids": ", ".join(ids),
        "question": (question or "").strip() or "\u8bf7\u68c0\u67e5\u8be5\u5c3d\u8c03\u62a5\u544a\u586b\u5199\u662f\u5426\u6709\u95ee\u9898\u3002",
        "rubric_guidance": rubric_guidance(rubric),
        "report_text": (report_text or "").strip() or "(none)",
        "report_json": _pretty_json(report_json) or "(none)",
        "attachments": att_block,
    }
    try:
        parsed, sources = _run_model(
            settings=settings,
            rubric=rubric,
            system_t=system_t,
            user_t=user_t,
            mapping_base=mapping_base,
            llm_fn=llm_fn,
            kb_opener=kb_opener,
            progress_fn=progress_fn,
        )
    except LlmError as exc:
        return _empty_fail(str(exc))

    sources = _merge_sources(
        sources,
        parsed.get("sources") if isinstance(parsed.get("sources"), list) else [],
    )
    summary = str(parsed.get("summary") or "").strip()
    findings: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    scores_raw: Dict[str, Any] = {}
    score: Any = None
    if pack.output == "conflicts":
        conflicts = _normalize_conflicts(parsed.get("conflicts"))
        obvious = sum(1 for c in conflicts if c["verdict"] == "obvious")
        suspected = sum(1 for c in conflicts if c["verdict"] == "suspected")
        try:
            obvious = int(parsed.get("obvious_count") or obvious)
        except (TypeError, ValueError):
            pass
        try:
            suspected = int(parsed.get("suspected_count") or suspected)
        except (TypeError, ValueError):
            pass
    else:
        _progress("score")
        scores_raw = parsed.get("dimension_scores") if isinstance(parsed.get("dimension_scores"), dict) else {}
        score = aggregate_score(scores_raw, rubric)
        findings = _normalize_findings(parsed.get("findings"), ids)
        obvious = 0
        suspected = 0

    files: List[Dict[str, Any]] = []
    word_detail = ""
    history_info: Dict[str, Any] = {}
    _progress("word")
    try:
        docx_bytes = _render_word(
            pack=pack,
            rubric=rubric,
            score=score,
            summary=summary,
            findings=findings,
            conflicts=conflicts,
            sources=sources,
        )
        mime = str((rubric.get("word") or {}).get("mime") or WORD_CONTENT_TYPE)
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
        if (report_id or "").strip() and files and not word_detail:
            try:
                saver = persist_fn or persist_check
                history_info = saver(
                    settings,
                    report_id=str(report_id).strip(),
                    scenario=pack.scenario_id,
                    scenario_title=pack.title,
                    filename=filename,
                    content_type=mime,
                    docx_bytes=docx_bytes,
                )
            except Exception as exc:
                history_info = {"ok": False, "detail": str(exc)}
    except Exception as exc:
        word_detail = f"word render/upload failed: {exc}"

    body: Dict[str, Any] = {
        "ok": True,
        "report_id": (report_id or "").strip(),
        "scenario": pack.scenario_id,
        "scenario_title": pack.title,
        "summary": summary,
        "sources": sources,
        "files": files,
        "attachment_skipped": skipped,
    }
    if pack.output == "conflicts":
        body["conflicts"] = conflicts
        body["obvious_count"] = obvious
        body["suspected_count"] = suspected
    else:
        body["score"] = score
        body["findings"] = findings
        body["dimension_scores"] = {k: scores_raw[k] for k in scores_raw}
    if history_info.get("check_id"):
        body["check_id"] = history_info.get("check_id")
    if word_detail:
        body["word_detail"] = word_detail
    if history_info and not history_info.get("ok"):
        body["history_detail"] = history_info.get("detail") or ""
    return body
