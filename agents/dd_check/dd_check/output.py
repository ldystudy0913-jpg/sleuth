"""Generated-file upload. Same S3-compatible COS shape as Sleuth; own env prefix."""
from __future__ import annotations

import json
import re
from typing import Any, Dict
from urllib.parse import urlparse

from .config import Settings


def _http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _safe_name(filename: str) -> str:
    name = (filename or "output.txt").strip() or "output.txt"
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.ASCII)
    return name or "output.txt"


def _put_bytes(settings: Settings, *, key: str, data: bytes, mime: str) -> str:
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError("boto3 is required for emit_file upload: pip install boto3") from exc
    kwargs: Dict[str, Any] = {
        "aws_access_key_id": settings.cos_secret_id,
        "aws_secret_access_key": settings.cos_secret_key,
    }
    if settings.cos_region:
        kwargs["region_name"] = settings.cos_region
    if settings.cos_endpoint:
        kwargs["endpoint_url"] = settings.cos_endpoint
    kwargs["config"] = BotoConfig(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
    )
    client = boto3.client("s3", **kwargs)
    extra: Dict[str, Any] = {}
    if mime:
        extra["ContentType"] = mime
    client.put_object(Bucket=settings.cos_bucket, Key=key, Body=data, **extra)
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.cos_bucket, "Key": key},
            ExpiresIn=300,
            HttpMethod="GET",
        )
    except Exception:
        return ""


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
    name = _safe_name(filename)
    href = (url or "").strip()
    key = (object_key or "").strip()
    body = content if isinstance(content, str) else ""
    mime_s = (mime or "text/plain").strip() or "text/plain"
    if href:
        if not _http_url(href) or href.startswith("data:") or href.lower().startswith("file:"):
            return {
                "ok": False,
                "detail": "url must be http(s); data: and file: are not allowed",
                "files": [],
            }
    raw = content_bytes if content_bytes is not None else (body.encode("utf-8") if body else b"")
    if raw and not href and not key:
        prefix = (settings.cos_path_prefix or "sleuth/files").strip().strip("/")
        key = f"{prefix}/{name}" if prefix else name
        try:
            href = _put_bytes(settings, key=key, data=raw, mime=mime_s)
        except Exception as exc:
            return {"ok": False, "detail": f"upload failed: {exc}", "files": []}
        size = len(raw)
    if not href and not key:
        return {
            "ok": False,
            "detail": "provide content to upload, or https url / object_key",
            "files": [],
        }
    entry: Dict[str, Any] = {
        "filename": name,
        "mime": mime_s,
        "size": int(size or 0),
    }
    if href:
        entry["url"] = href
    if key:
        entry["object_key"] = key
    return {"ok": True, "files": [entry]}


def register(server: Any, settings: Settings) -> None:
    @server.tool(
        name="emit_file",
        description=(
            "Upload or register a generated file for the Sleuth session mailbox. "
            "Pass content to upload via this agent's COS, or filename plus https url / object_key. "
            "Return JSON files[]. Do not embed bytes or data-URLs."
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
