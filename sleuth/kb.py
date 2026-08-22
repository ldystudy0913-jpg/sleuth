"""Remote knowledge-base search for the default agent (SLEUTH_KB_*).

Login POST ``{openId, serviceId}`` → Cookie ``ragToken``; search POST
``{question, serviceConfig?}``. Same intranet API as dd_reply.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .config import Config, KbConfig


class KbError(RuntimeError):
    """Remote KB HTTP / protocol failure."""


_cached_rag_token: Optional[str] = None
_cached_expire_ms: int = 0
_cached_token_key: str = ""
_token_lock = threading.Lock()


def reset_token_cache() -> None:
    """Drop the process-wide ragToken cache (tests)."""
    global _cached_rag_token, _cached_expire_ms, _cached_token_key
    with _token_lock:
        _cached_rag_token = None
        _cached_expire_ms = 0
        _cached_token_key = ""


def kb_config(config: Config) -> KbConfig:
    return getattr(config, "kb", None) or KbConfig()


def _csv(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _json_headers() -> Dict[str, str]:
    return {"Content-Type": "application/json", "Accept": "application/json"}


def _token_cache_key(kb: KbConfig) -> str:
    return "|".join((kb.login_url or "", kb.openid or "", kb.service_id or ""))


def _normalize_expire_ms(expire_time: Any) -> int:
    try:
        val = int(expire_time)
    except (TypeError, ValueError):
        return 0
    if val <= 0:
        return 0
    if val < 10_000_000_000:
        if val < 10_000_000:
            return int(time.time() * 1000) + val * 1000
        return val * 1000
    return val


def _post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    opener=None,
) -> Tuple[int, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        if opener is not None:
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        with resp:
            raw = resp.read()
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = ""
        raise KbError(f"KB HTTP {exc.code}: {err_body}") from exc
    except urllib.error.URLError as exc:
        raise KbError(f"KB request failed: {exc}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KbError("KB response is not JSON") from exc
    return int(status), parsed


def _fetch_kb_token(kb: KbConfig, opener=None) -> Tuple[str, int]:
    if not (kb.login_url or "").strip():
        raise KbError("SLEUTH_KB_LOGIN_URL is not configured")
    status, data = _post_json(
        kb.login_url,
        {"openId": kb.openid, "serviceId": kb.service_id},
        _json_headers(),
        float(kb.api_timeout or 30),
        opener=opener,
    )
    if status >= 400:
        raise KbError(f"KB login HTTP {status}")
    if not isinstance(data, dict) or data.get("returnCode") != "SUC0000":
        raise KbError(f"KB login failed, response: {data}")
    body = data.get("body") or {}
    if not isinstance(body, dict):
        raise KbError("KB login body must be an object")
    token = str(body.get("ragToken") or "").strip()
    if not token:
        raise KbError("KB login missing ragToken")
    return token, _normalize_expire_ms(body.get("expireTime"))


def _auth_headers(kb: KbConfig, opener=None) -> Dict[str, str]:
    global _cached_rag_token, _cached_expire_ms, _cached_token_key
    headers = _json_headers()
    key = _token_cache_key(kb)
    now_ms = int(time.time() * 1000)
    refresh_at = _cached_expire_ms - 5 * 60 * 1000
    if (
        not _cached_rag_token
        or _cached_token_key != key
        or now_ms >= refresh_at
    ):
        with _token_lock:
            now_ms2 = int(time.time() * 1000)
            refresh_at2 = _cached_expire_ms - 5 * 60 * 1000
            if (
                not _cached_rag_token
                or _cached_token_key != key
                or now_ms2 >= refresh_at2
            ):
                token, expire_ms = _fetch_kb_token(kb, opener=opener)
                _cached_rag_token = token
                _cached_expire_ms = expire_ms
                _cached_token_key = key
    headers["Cookie"] = f"ragToken={_cached_rag_token}"
    return headers


def _build_time_filter(kb: KbConfig) -> Optional[Dict[str, Any]]:
    if not kb.time_filter_enable:
        return None
    if (
        kb.time_filter_by_day is None
        and not kb.time_filter_start_time
        and not kb.time_filter_end_time
    ):
        return None
    out: Dict[str, Any] = {"timeFilterEnable": True}
    if kb.time_filter_by_day is not None:
        out["byDay"] = kb.time_filter_by_day
    if kb.time_filter_start_time:
        out["byTimeStartTime"] = kb.time_filter_start_time
    if kb.time_filter_end_time:
        out["byTimeEndTime"] = kb.time_filter_end_time
    return out


def _build_service_config(kb: KbConfig) -> Optional[Dict[str, Any]]:
    service: Dict[str, Any] = {}
    sort: Dict[str, Any] = {}
    if int(kb.sort_count) != 10:
        sort["sortCount"] = int(kb.sort_count)
    if kb.sort_score is not None:
        sort["sortScore"] = float(kb.sort_score)
    if kb.time_combine:
        sort["timeCombine"] = True
    if sort:
        service["sortConfig"] = sort

    knowledge_ids = _csv(kb.knowledge_ids)
    if knowledge_ids:
        time_filter = _build_time_filter(kb)
        atom_ids = _csv(kb.atom_ids)
        node_ids = _csv(kb.node_ids)
        recalls: List[Dict[str, Any]] = []
        for kid in knowledge_ids:
            rc: Dict[str, Any] = {"knowledgeId": kid}
            if int(kb.recall_count) != 10:
                rc["recallCount"] = int(kb.recall_count)
            if atom_ids:
                rc["atomIds"] = atom_ids
            if node_ids:
                rc["nodeIds"] = node_ids
            if kb.html_clear:
                rc["htmlClear"] = True
            if kb.qa_search_mode:
                rc["qaSearchMode"] = kb.qa_search_mode
            if time_filter:
                rc["timeFilterConfig"] = time_filter
            recalls.append(rc)
        service["recallConfig"] = recalls

    tag_ids = _csv(kb.tag_value_ids)
    tag_names = _csv(kb.tag_value_names)
    if kb.tag_name and tag_ids and tag_names:
        service["tagConfig"] = [
            {
                "tagName": kb.tag_name,
                "tagValueIds": tag_ids,
                "tagValueNames": tag_names,
                "tagSearchOperation": kb.tag_search_operation or "AND",
            }
        ]

    if kb.subnet_type and kb.subnet_type != "dmz":
        service["subnetType"] = kb.subnet_type
    return service or None


def _source_url(item: Dict[str, Any]) -> str:
    dmz = str(item.get("dmzUrl") or item.get("dmz_url") or "").strip()
    if dmz:
        return dmz
    for key in ("fileUrl", "file_url", "url", "toolUrl"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    splits = item.get("splitContents") or []
    if isinstance(splits, list):
        for sc in splits:
            if isinstance(sc, dict):
                val = str(sc.get("url") or "").strip()
                if val:
                    return val
    return ""


def _hit_from_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    try:
        score = float(item.get("rankScore") or item.get("rank_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "title": str(item.get("title") or ""),
        "file_name": str(item.get("fileName") or item.get("file_name") or ""),
        "url": _source_url(item),
        "knowledge_id": str(item.get("knowledgeId") or item.get("knowledge_id") or ""),
        "rank_score": score,
        "paragraph": str(item.get("paragraph") or item.get("content") or ""),
        "dmz_url": str(item.get("dmzUrl") or item.get("dmz_url") or ""),
    }


def search_knowledge(
    question: str,
    config: Config,
    *,
    opener=None,
) -> List[Dict[str, Any]]:
    kb = kb_config(config)
    q = (question or "").strip()
    if not q:
        raise KbError("question is required")
    if not kb.configured():
        raise KbError(
            "SLEUTH_KB_API_URL, SLEUTH_KB_LOGIN_URL, SLEUTH_KB_OPENID, "
            "and SLEUTH_KB_SERVICEID are required for default-agent knowledge search"
        )
    body: Dict[str, Any] = {"question": q}
    service = _build_service_config(kb)
    if service:
        body["serviceConfig"] = service
    timeout = float(kb.api_timeout or 30)
    headers = _auth_headers(kb, opener=opener)
    _, payload = _post_json(kb.api_url, body, headers, timeout, opener=opener)
    if not isinstance(payload, dict):
        raise KbError("KB response root must be an object")
    code = str(payload.get("returnCode") or "")
    if code and code != "SUC0000":
        raise KbError(
            f"KB returnCode={code} errorMsg={payload.get('errorMsg')!r}"
        )
    body_list = payload.get("body")
    if body_list is None:
        return []
    if not isinstance(body_list, list):
        raise KbError("KB body must be a list")
    hits = [_hit_from_dict(x) for x in body_list if isinstance(x, dict)]
    hits.sort(key=lambda h: float(h.get("rank_score") or 0), reverse=True)
    cap = int(kb.sort_count or 0)
    if cap > 0:
        hits = hits[:cap]
    return hits
