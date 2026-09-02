"""Optional knowledge-base stub. Fill __ENV_PREFIX___KB_API_URL; return Sleuth sources[]."""
from __future__ import annotations

import json
from typing import Any, Dict, List
from urllib.parse import urlparse

from .config import Settings


def _http_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def search(question: str, settings: Settings) -> Dict[str, Any]:
    """TODO: POST settings.kb_api_url. Until then, empty sources[]."""
    q = (question or "").strip()
    api = str(getattr(settings, "kb_api_url", "") or "").strip()
    if not api:
        return {
            "ok": False,
            "question": q,
            "detail": "kb not configured; set __ENV_PREFIX___KB_API_URL",
            "sources": [],
        }
    parsed = urlparse(api)
    if parsed.scheme not in ("http", "https"):
        return {
            "ok": False,
            "question": q,
            "detail": "kb api url must be http(s)",
            "sources": [],
        }
    # Replace this with your retrieval HTTP call. Each hit: {title, url}.
    sources: List[Dict[str, str]] = []
    return {"ok": True, "question": q, "hits": [], "sources": sources}


def register(server: Any, settings: Settings) -> None:
    @server.tool(
        name="kb_search",
        description=(
            "Search this agent's knowledge base. Returns JSON with sources[] "
            "(title + http(s) url) for Sleuth to append as 知识来源. "
            "Not Sleuth kb_lookup — wire your own API via __ENV_PREFIX___KB_API_URL."
        ),
    )
    def kb_search(question: str = "") -> str:
        return json.dumps(search(question, settings), ensure_ascii=False)
