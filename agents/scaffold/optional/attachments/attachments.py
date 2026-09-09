"""Load session-file excerpts injected by Sleuth. Prefer excerpt; do not decrypt SM4."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _http_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _http_get_text(url: str, *, max_bytes: int = 65536, timeout: float = 10.0) -> str:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as resp:  # nosec B310 — caller already restricted to http(s)
        charset = resp.headers.get_content_charset() or "utf-8"
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode(charset, errors="replace")


def _clip(text: str, max_chars: int) -> str:
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def _excerpt_from_url(name: str, url: str, max_chars: int) -> Tuple[Optional[str], Optional[str]]:
    if url.startswith("data:") or url.startswith("file:"):
        return None, f"{name}:data/file URLs are not allowed"
    if not _http_url(url):
        return None, f"{name}:not an http(s) URL"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, f"{name}:blocked url"
    try:
        text = _http_get_text(url, max_bytes=max(max_chars, 1024)).strip()
    except Exception as exc:
        return None, f"{name}:download failed:{exc}"
    if not text:
        return None, f"{name}:empty download"
    return _clip(text, max_chars), None


def _one_excerpt(ref: Any, max_chars: int) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(ref, dict):
        return None, None
    name = str(ref.get("filename") or ref.get("file_id") or "attachment")
    excerpt = str(ref.get("excerpt") or "").strip()
    if excerpt:
        return _clip(excerpt, max_chars), None
    if ref.get("encrypted"):
        return None, f"{name}:encrypted; Sleuth decrypts and supplies excerpt"
    url = str(ref.get("url") or "").strip()
    if not url:
        return None, f"{name}:no excerpt"
    return _excerpt_from_url(name, url, max_chars)


def load_excerpts(refs: List[dict], *, max_chars: int = 8000) -> Tuple[List[str], List[str]]:
    """Return (excerpts, skipped reasons). Prefer Sleuth excerpt; skip ciphertext."""
    excerpts: List[str] = []
    skipped: List[str] = []
    for ref in refs or []:
        text, reason = _one_excerpt(ref, max_chars)
        if text:
            excerpts.append(text)
        elif reason:
            skipped.append(reason)
    return excerpts, skipped


def summarize_refs(refs: List[dict]) -> Dict[str, Any]:
    excerpts, skipped = load_excerpts(refs)
    return {
        "attachment_count": len([r for r in (refs or []) if isinstance(r, dict)]),
        "excerpt_count": len(excerpts),
        "excerpts": excerpts,
        "skipped": skipped,
    }
