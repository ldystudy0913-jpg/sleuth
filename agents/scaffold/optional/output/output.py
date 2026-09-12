"""把生成文件打成 Sleuth 会话邮箱要的 files[]。

Sleuth 负责加密上传。这里返回带 content_base64 的明文。
本包 COS 配齐才自动注册 emit_file；会话回传更推荐业务 JSON 直接带 files[]，不必配 COS。
本文件一般不用改。
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .config import Settings


def _http_url(url: str) -> bool:
    """是否可登记的 http(s) URL。"""
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _safe_name(filename: str) -> str:
    """去掉路径，只留安全文件名。"""
    name = (filename or "output.txt").strip() or "output.txt"
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.ASCII)
    return name or "output.txt"


def _reject_href(href: str) -> Optional[Dict[str, Any]]:
    """拒绝 data: / file: URL，避免把密文或本地路径交给基座。"""
    if not href:
        return None
    blocked = href.startswith("data:") or href.lower().startswith("file:")
    if not _http_url(href) or blocked:
        return {
            "ok": False,
            "detail": "url must be http(s); data: and file: are not allowed",
            "files": [],
        }
    return None


def _inline_file(name: str, mime_s: str, raw: bytes) -> Dict[str, Any]:
    """正文走 content_base64，由 Sleuth 加密进邮箱。"""
    return {
        "ok": True,
        "files": [
            {
                "filename": name,
                "mime": mime_s,
                "size": len(raw),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        ],
    }


def _ref_file(name: str, mime_s: str, raw: bytes, href: str, key: str, size: int) -> Dict[str, Any]:
    """已有 https / object_key 时只登记，不重写对象。"""
    entry: Dict[str, Any] = {
        "filename": name,
        "mime": mime_s,
        "size": int(size or (len(raw) if raw else 0)),
    }
    if href:
        entry["url"] = href
    if key:
        entry["object_key"] = key
    return {"ok": True, "files": [entry]}


def emit_file(
    settings: Settings,
    *,
    filename: str,
    content: str = "",
    content_bytes: bytes | None = None,
    url: str = "",
    mime: str = "text/plain",
    object_key: str = "",
    size: int = 0,
) -> Dict[str, Any]:
    """打包一个 files[] 条目。settings 预留签名，实际上传由 Sleuth 做。"""
    del settings  # mailbox upload is Sleuth's job; keep the call signature
    name = _safe_name(filename)
    href = (url or "").strip()
    key = (object_key or "").strip()
    body = content if isinstance(content, str) else ""
    mime_s = (mime or "text/plain").strip() or "text/plain"
    rejected = _reject_href(href)
    if rejected:
        return rejected
    raw = content_bytes if content_bytes is not None else (body.encode("utf-8") if body else b"")
    if raw and not href and not key:
        return _inline_file(name, mime_s, raw)
    if not href and not key:
        return {
            "ok": False,
            "detail": "provide content to return, or https url / object_key",
            "files": [],
        }
    return _ref_file(name, mime_s, raw, href, key, size)


def register(server: Any, settings: Settings) -> None:
    """COS 配齐时由 mcp_server 调用，注册 emit_file。一般不用自己调。"""
    @server.tool(
        name="emit_file",
        description=(
            "Package a generated file for the Sleuth session mailbox. "
            "Pass content (Sleuth encrypts and stores) or filename plus https url / object_key. "
            "Return JSON files[]. Do not embed data-URLs."
        ),
    )
    def emit_file_tool(
        filename: str = "output.txt",
        content: str = "",
        url: str = "",
        mime: str = "text/plain",
        object_key: str = "",
        size: int = 0,
    ) -> str:
        """MCP 工具入口：把正文打成 files[].content_base64。"""
        return json.dumps(
            emit_file(
                settings,
                filename=filename,
                content=content,
                url=url,
                mime=mime,
                object_key=object_key,
                size=size,
            ),
            ensure_ascii=False,
        )
