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


def load_excerpts(refs: List[dict], *, max_chars: int = 8000) -> Tuple[List[str], List[str]]:
    """Return (excerpts, skipped reasons). Prefer Sleuth excerpt; skip ciphertext."""
    excerpts: List[str] = []
    skipped: List[str] = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        name = str(ref.get("filename") or ref.get("file_id") or "attachment")
        excerpt = str(ref.get("excerpt") or "").strip()
        if excerpt:
            if max_chars > 0 and len(excerpt) > max_chars:
                excerpt = excerpt[:max_chars]
            excerpts.append(excerpt)
            continue
        if ref.get("encrypted"):
            skipped.append(f"{name}:encrypted; Sleuth decrypts and supplies excerpt")
            continue
        url = str(ref.get("url") or "").strip()
        if not url:
            skipped.append(f"{name}:no excerpt")
            continue
        if url.startswith("data:") or url.startswith("file:"):
            skipped.append(f"{name}:data/file URLs are not allowed")
            continue
        if not _http_url(url):
            skipped.append(f"{name}:not an http(s) URL")
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            skipped.append(f"{name}:blocked url")
            continue
        try:
            text = _http_get_text(url, max_bytes=max(max_chars, 1024)).strip()
        except Exception as exc:
            skipped.append(f"{name}:download failed:{exc}")
            continue
        if not text:
            skipped.append(f"{name}:empty download")
            continue
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
        excerpts.append(text)
    return excerpts, skipped


def summarize_refs(refs: List[dict]) -> Dict[str, Any]:
    excerpts, skipped = load_excerpts(refs)
    return {
        "attachment_count": len([r for r in (refs or []) if isinstance(r, dict)]),
        "excerpt_count": len(excerpts),
        "excerpts": excerpts,
        "skipped": skipped,
    }
