"""Knowledge-base search. Same intranet RAG protocol as Sleuth kb_lookup; own env prefix."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .config import Settings

_cached_rag_token: Optional[str] = None
_cached_expire_ms: int = 0
_cached_token_key: str = ""
_token_lock = threading.Lock()


def reset_token_cache() -> None:
    global _cached_rag_token, _cached_expire_ms, _cached_token_key
    with _token_lock:
        _cached_rag_token = None
        _cached_expire_ms = 0
        _cached_token_key = ""


class KbError(RuntimeError):
    """Remote KB HTTP / protocol failure."""


def _csv(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _json_headers() -> Dict[str, str]:
    return {"Content-Type": "application/json", "Accept": "application/json"}


def _token_cache_key(settings: Settings) -> str:
    return "|".join((settings.kb_login_url, settings.kb_openid, settings.kb_service_id))


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


def _fetch_kb_token(settings: Settings, opener=None) -> Tuple[str, int]:
    status, data = _post_json(
        settings.kb_login_url,
        {"openId": settings.kb_openid, "serviceId": settings.kb_service_id},
        _json_headers(),
        float(settings.kb_api_timeout or 30),
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


def _auth_headers(settings: Settings, opener=None) -> Dict[str, str]:
    global _cached_rag_token, _cached_expire_ms, _cached_token_key
    headers = _json_headers()
    key = _token_cache_key(settings)
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
                token, expire_ms = _fetch_kb_token(settings, opener=opener)
                _cached_rag_token = token
                _cached_expire_ms = expire_ms
                _cached_token_key = key
    headers["Cookie"] = f"ragToken={_cached_rag_token}"
    return headers


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
        "rank_score": score,
    }


def _service_config(settings: Settings) -> Optional[Dict[str, Any]]:
    service: Dict[str, Any] = {}
    knowledge_ids = _csv(settings.kb_knowledge_ids)
    if knowledge_ids:
        recalls = []
        for kid in knowledge_ids:
            rc: Dict[str, Any] = {"knowledgeId": kid}
            if int(settings.kb_recall_count) != 10:
                rc["recallCount"] = int(settings.kb_recall_count)
            recalls.append(rc)
        service["recallConfig"] = recalls
    if int(settings.kb_sort_count) != 10:
        service["sortConfig"] = {"sortCount": int(settings.kb_sort_count)}
    return service or None


def _http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def search(question: str, settings: Settings, *, opener=None) -> Dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return {"ok": False, "question": q, "detail": "question is required", "sources": []}
    body: Dict[str, Any] = {"question": q}
    service = _service_config(settings)
    if service:
        body["serviceConfig"] = service
    try:
        headers = _auth_headers(settings, opener=opener)
        _, payload = _post_json(
            settings.kb_api_url,
            body,
            headers,
            float(settings.kb_api_timeout or 30),
            opener=opener,
        )
    except KbError as exc:
        return {"ok": False, "question": q, "detail": str(exc), "sources": []}
    if not isinstance(payload, dict):
        return {"ok": False, "question": q, "detail": "KB response root must be an object", "sources": []}
    code = str(payload.get("returnCode") or "")
    if code and code != "SUC0000":
        return {
            "ok": False,
            "question": q,
            "detail": f"KB returnCode={code}",
            "sources": [],
        }
    body_list = payload.get("body")
    if body_list is None:
        return {"ok": True, "question": q, "hits": [], "sources": []}
    if not isinstance(body_list, list):
        return {"ok": False, "question": q, "detail": "KB body must be a list", "sources": []}
    hits = [_hit_from_dict(x) for x in body_list if isinstance(x, dict)]
    hits.sort(key=lambda h: float(h.get("rank_score") or 0), reverse=True)
    cap = int(settings.kb_sort_count or 0)
    if cap > 0:
        hits = hits[:cap]
    sources: List[Dict[str, str]] = []
    seen = set()
    for hit in hits:
        url = str(hit.get("url") or "").strip()
        if not _http_url(url):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        title = str(hit.get("file_name") or hit.get("title") or "").strip() or key
        sources.append({"title": title, "url": url})
    return {"ok": True, "question": q, "hits": hits, "sources": sources}


def register(server: Any, settings: Settings) -> None:
    @server.tool(
        name="kb_search",
        description=(
            "Search this agent's knowledge base. Returns JSON with sources[] "
            "(title + http(s) url) for Sleuth to append as 知识来源."
        ),
    )
    def kb_search(question: str = "") -> str:
        return json.dumps(search(question, settings), ensure_ascii=False)
