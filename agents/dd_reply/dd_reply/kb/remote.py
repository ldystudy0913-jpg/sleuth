from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import Settings
from .models import (
    KbHit,
    RecallConfig,
    RiskRetrieval,
    SearchRequest,
    ServiceConfig,
    SortConfig,
    TagConfig,
    TimeFilterConfig,
)
from ..models import normalize_risk_query

logger = logging.getLogger(__name__)

_cached_rag_token: Optional[str] = None
_cached_expire_ms: int = 0
_cached_token_key: str = ""
_token_lock = threading.Lock()


class KbApiError(RuntimeError):
    """Remote KB HTTP / protocol failure."""


def reset_token_cache() -> None:
    """Drop the process-wide ragToken cache (tests)."""
    global _cached_rag_token, _cached_expire_ms, _cached_token_key
    with _token_lock:
        _cached_rag_token = None
        _cached_expire_ms = 0
        _cached_token_key = ""


def _token_cache_key(settings: Settings) -> str:
    return "|".join(
        (
            settings.kb_login_url or "",
            settings.kb_login_openid or "",
            settings.kb_login_service_id or "",
        )
    )


def _normalize_expire_ms(expire_time: Any) -> int:
    """Accept epoch seconds, epoch ms, or a short TTL in seconds."""
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


def _fetch_kb_token(settings: Settings, http: httpx.Client) -> Tuple[str, int]:
    if not settings.kb_login_url:
        raise KbApiError("DD_REPLY_KB_LOGIN_URL is not configured")
    payload = {
        "openId": settings.kb_login_openid,
        "serviceId": settings.kb_login_service_id,
    }
    try:
        resp = http.post(settings.kb_login_url, json=payload)
    except httpx.HTTPError as exc:
        raise KbApiError(f"KB login failed: {exc}") from exc
    if resp.status_code >= 400:
        raise KbApiError(
            f"KB login HTTP {resp.status_code}: {(resp.text or '')[:500]}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise KbApiError("KB login response is not JSON") from exc
    if not isinstance(data, dict) or data.get("returnCode") != "SUC0000":
        raise KbApiError(f"KB login failed, response: {data}")
    body = data.get("body") or {}
    if not isinstance(body, dict):
        raise KbApiError("KB login body must be an object")
    token = str(body.get("ragToken") or "").strip()
    if not token:
        raise KbApiError("KB login missing ragToken")
    return token, _normalize_expire_ms(body.get("expireTime"))


def _auth_headers(settings: Settings, http: httpx.Client) -> Dict[str, str]:
    global _cached_rag_token, _cached_expire_ms, _cached_token_key
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
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
                token, expire_ms = _fetch_kb_token(settings, http)
                _cached_rag_token = token
                _cached_expire_ms = expire_ms
                _cached_token_key = key
    headers["Cookie"] = f"ragToken={_cached_rag_token}"
    return headers


def _csv(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _build_time_filter_config(settings: Settings) -> Optional[TimeFilterConfig]:
    if not settings.kb_time_filter_enable:
        return None
    config = TimeFilterConfig(
        time_filter_enable=True,
        by_day=settings.kb_time_filter_by_day,
        by_time_start_time=settings.kb_time_filter_start_time or None,
        by_time_end_time=settings.kb_time_filter_end_time or None,
    )
    if (
        config.by_day is None
        and not config.by_time_start_time
        and not config.by_time_end_time
    ):
        return None
    return config


def _build_sort_config(settings: Settings) -> Optional[SortConfig]:
    config = SortConfig()
    if settings.kb_sort_count != 10:
        config.sort_count = settings.kb_sort_count
    if settings.kb_sort_score is not None:
        config.sort_score = settings.kb_sort_score
    if settings.kb_time_combine:
        config.time_combine = settings.kb_time_combine
    if (
        config.sort_count is None
        and config.sort_score is None
        and not config.time_combine
    ):
        return None
    return config


def _build_recall_config(settings: Settings) -> List[RecallConfig]:
    knowledge_ids = _csv(settings.kb_knowledge_ids)
    node_ids = _csv(settings.kb_node_ids)
    atom_ids = _csv(settings.kb_atom_ids)
    if not knowledge_ids:
        return []
    configs: List[RecallConfig] = []
    for knowledge_id in knowledge_ids:
        configs.append(
            RecallConfig(
                knowledge_id=knowledge_id,
                recall_count=(
                    settings.kb_recall_count
                    if settings.kb_recall_count != 10
                    else None
                ),
                atom_ids=atom_ids or None,
                node_ids=node_ids or None,
                html_clear=True if settings.kb_html_clear else None,
                qa_search_mode=settings.kb_qa_search_mode or None,
                time_filter_config=_build_time_filter_config(settings),
            )
        )
    return configs


def _build_tag_config(settings: Settings) -> Optional[List[TagConfig]]:
    if not settings.kb_tag_name:
        return None
    tag_value_ids = _csv(settings.kb_tag_value_ids)
    tag_value_names = _csv(settings.kb_tag_value_names)
    if not tag_value_ids or not tag_value_names:
        return None
    return [
        TagConfig(
            tag_name=settings.kb_tag_name,
            tag_value_ids=tag_value_ids,
            tag_value_names=tag_value_names,
            tag_search_operation=settings.kb_tag_search_operation or "AND",
        )
    ]


def _build_service_config(settings: Settings) -> Optional[ServiceConfig]:
    sort_config = _build_sort_config(settings)
    recall_config = _build_recall_config(settings)
    tag_config = _build_tag_config(settings)
    if not any([sort_config, recall_config, tag_config]):
        subnet = settings.kb_subnet_type if settings.kb_subnet_type != "dmz" else None
        if not subnet:
            return None
        return ServiceConfig(subnet_type=subnet)
    return ServiceConfig(
        sort_config=sort_config,
        recall_config=recall_config or None,
        tag_config=tag_config,
        subnet_type=(
            settings.kb_subnet_type if settings.kb_subnet_type != "dmz" else None
        ),
    )


def search_knowledge(
    question: str,
    settings: Settings,
    *,
    client: Optional[httpx.Client] = None,
) -> List[KbHit]:
    """POST remote KB with required ``question`` (+ optional serviceConfig)."""
    q = (question or "").strip()
    if not q:
        raise KbApiError("question is required")
    if not settings.kb_api_configured():
        raise KbApiError(
            "DD_REPLY_KB_API_URL, DD_REPLY_KB_LOGIN_URL, "
            "DD_REPLY_KB_OPENID, and DD_REPLY_KB_SERVICEID are required"
        )

    service_config = _build_service_config(settings)
    search_request = SearchRequest(question=q, service_config=service_config)
    body = search_request.to_dict()
    timeout = float(settings.kb_api_timeout or 30)

    logger.debug("KB request: %s %s", settings.kb_api_url, json.dumps(body, ensure_ascii=False))

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        headers = _auth_headers(settings, http)
        try:
            resp = http.post(settings.kb_api_url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise KbApiError(f"KB request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise KbApiError(
                f"KB HTTP {resp.status_code}: {(resp.text or '')[:500]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise KbApiError("KB response is not JSON") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(data, dict):
        raise KbApiError("KB response root must be an object")

    code = str(data.get("returnCode") or "")
    if code and code != "SUC0000":
        raise KbApiError(
            f"KB returnCode={code} errorMsg={data.get('errorMsg')!r}"
        )

    body_list = data.get("body")
    if body_list is None:
        return []
    if not isinstance(body_list, list):
        raise KbApiError("KB body must be a list")

    hits: List[KbHit] = []
    for item in body_list:
        if isinstance(item, dict):
            hits.append(KbHit.from_dict(item))
    hits.sort(
        key=lambda h: (h.final_response, h.comprehended, h.rank_score),
        reverse=True,
    )
    cap = int(settings.kb_sort_count or 0)
    if cap > 0:
        hits = hits[:cap]
    return hits


def retrieve_risk_code(
    code: str,
    settings: Settings,
    *,
    client: Optional[httpx.Client] = None,
    question: Optional[str] = None,
) -> RiskRetrieval:
    """Search KB for one risk code or name (question = the query string)."""
    q = normalize_risk_query(question if question is not None else code)
    c = normalize_risk_query(code) or q
    try:
        hits = search_knowledge(q, settings, client=client)
    except KbApiError as exc:
        logger.warning("KB search failed for %s: %s", c, exc)
        return RiskRetrieval(code=c, question=q, hits=[], error=str(exc), source="remote")
    return RiskRetrieval(code=c, question=q, hits=hits, error="", source="remote")


def retrieve_risk_codes(
    codes: List[str],
    settings: Settings,
    *,
    client: Optional[httpx.Client] = None,
) -> List[RiskRetrieval]:
    """Batch retrieve; one HTTP call per distinct code or name."""
    seen: set[str] = set()
    ordered: List[str] = []
    for raw in codes:
        c = normalize_risk_query(raw)
        if not c or c in seen:
            continue
        seen.add(c)
        ordered.append(c)

    owns = client is None and settings.kb_api_configured()
    http = client
    if owns:
        http = httpx.Client(timeout=float(settings.kb_api_timeout or 30))
    try:
        return [
            retrieve_risk_code(c, settings, client=http) for c in ordered
        ]
    finally:
        if owns and http is not None:
            http.close()
