"""Harvest citation sources from tool JSON — agent-agnostic, like ``files[]``.

Agents return structured ``sources[]`` (title + url). Sleuth does not parse
any agent's markdown or heading conventions.
"""
from __future__ import annotations

import json
from html import escape
from typing import Any, Dict, Iterable, List, Optional

_GRAY = "color:#888"
_SOURCES_HEADING = "知识来源"


def _https_or_http(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _norm_url(url: str) -> str:
    """Identity for dedup: trim whitespace and a single trailing slash."""
    return (url or "").strip().rstrip("/")


def normalize_source_item(raw: Any) -> Optional[Dict[str, str]]:
    """Map one tool payload item to ``{title, url}``. Strings that are not URLs are ignored."""
    if isinstance(raw, str):
        url = raw.strip()
        if not _https_or_http(url):
            return None
        return {"title": url, "url": url}
    if not isinstance(raw, dict):
        return None
    title = str(
        raw.get("title")
        or raw.get("file_name")
        or raw.get("filename")
        or raw.get("name")
        or ""
    ).strip()
    url = str(raw.get("url") or raw.get("href") or raw.get("link") or "").strip()
    if not _https_or_http(url):
        return None
    if not title:
        title = url
    return {"title": title, "url": url}


def harvest_tool_sources(payload: Any) -> List[Dict[str, str]]:
    """Read top-level JSON ``sources[]`` only — not nested agent-specific trees."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("sources")
    if not isinstance(raw, list):
        return []
    return merge_sources([], (normalize_source_item(x) for x in raw))


def merge_sources(
    existing: Iterable[Dict[str, str]],
    incoming: Iterable[Optional[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """Keep first occurrence of each URL (same link, any title) and each title+url pair."""
    out: List[Dict[str, str]] = []
    seen_url: set[str] = set()
    seen_pair: set[str] = set()
    for item in list(existing) + [x for x in incoming if x]:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title and not url:
            continue
        url_key = _norm_url(url)
        pair_key = f"{title}|{url_key}"
        if (url_key and url_key in seen_url) or pair_key in seen_pair:
            continue
        if url_key:
            seen_url.add(url_key)
        seen_pair.add(pair_key)
        out.append({"title": title or url, "url": url})
    return out


def collect_sources(*, output: str = "", metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Combine ``ToolResult.metadata['sources']`` and JSON ``sources[]`` in ``output``."""
    items: List[Dict[str, str]] = []
    meta = metadata or {}
    extra = meta.get("sources")
    if isinstance(extra, list):
        items = merge_sources(items, (normalize_source_item(x) for x in extra))
    raw = (output or "").strip()
    if raw.startswith("{") or raw.startswith("["):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            items = merge_sources(items, harvest_tool_sources(payload))
    return items


def format_sources_footer(
    sources: Iterable[Dict[str, str]],
    *,
    existing_text: str = "",
) -> str:
    """Gray clickable appendix. Skip items whose URL is already in the assistant text."""
    existing = existing_text or ""
    lines: List[str] = []
    seen_url: set[str] = set()
    for item in sources:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        url_key = _norm_url(url)
        if url and (url in existing or url_key in existing):
            continue
        if url_key and url_key in seen_url:
            continue
        if url_key:
            seen_url.add(url_key)
        name = escape(title or url)
        if url:
            href = escape(url, quote=True)
            inner = f"《{name}》：<a href=\"{href}\" style=\"{_GRAY}\">{href}</a>"
        else:
            inner = f"《{name}》"
        lines.append(f'<span style="{_GRAY}">{inner}</span>')
    if not lines:
        return ""
    heading = f'<span style="{_GRAY}">{escape(_SOURCES_HEADING)}</span>'
    return "\n\n---\n\n" + heading + "\n\n" + "\n".join(lines)
