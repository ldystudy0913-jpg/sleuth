"""读 Sleuth 注入的会话摘录。优先 excerpt，不要解 SM4。

开 ATTACHMENTS=1 只让演示 ping 声明 attachment_refs_json。
新业务工具必须自己声明该参数，再在 pipeline 里调 summarize_refs / load_excerpts。
本文件一般不用改。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _http_url(url: str) -> bool:
    """是否 http(s)。data/file URL 一律拒绝。"""
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _http_get_text(url: str, *, max_bytes: int = 65536, timeout: float = 10.0) -> str:
    """仅当没有 excerpt 时回退下载明文 URL（Sleuth 下载路径，不是 COS 密文）。"""
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as resp:  # nosec B310 — caller already restricted to http(s)
        charset = resp.headers.get_content_charset() or "utf-8"
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode(charset, errors="replace")


def _clip(text: str, max_chars: int) -> str:
    """截断过长摘录，避免撑爆模型上下文。"""
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def _excerpt_from_url(name: str, url: str, max_chars: int) -> Tuple[Optional[str], Optional[str]]:
    """从 http(s) 拉文本。失败返回 (None, 原因)。"""
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
    """单条 ref：有 excerpt 直接用；encrypted 且无摘录则跳过（应等 Sleuth 注入）。"""
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
    """返回 (摘录列表, 跳过原因)。优先 Sleuth excerpt，跳过密文。"""
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
    """给 pipeline 用的附件摘要字段，可并进返回 JSON。"""
    excerpts, skipped = load_excerpts(refs)
    return {
        "attachment_count": len([r for r in (refs or []) if isinstance(r, dict)]),
        "excerpt_count": len(excerpts),
        "excerpts": excerpts,
        "skipped": skipped,
    }
