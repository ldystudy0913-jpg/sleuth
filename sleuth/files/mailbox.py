"""Session file mailbox: metadata in session.metadata.files, bytes in COS."""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from ..config import Config, FilesConfig
from ..util.ids import file_id as new_file_id
from .cos import CosError, CosNotConfigured, ObjectStore, object_store_from_config

_UNSAFE_NAME = re.compile(r"[^\w.\-()+\[\]\u4e00-\u9fff]+", re.UNICODE)


class MailboxError(ValueError):
    """User-facing mailbox validation error (maps to 400/413)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = int(status)


def files_config(config: Config) -> FilesConfig:
    return getattr(config, "files", None) or FilesConfig()


def safe_filename(name: str) -> str:
    raw = (name or "").replace("\\", "/").split("/")[-1].strip()
    raw = raw.lstrip(".")
    cleaned = _UNSAFE_NAME.sub("_", raw).strip("._") or "file"
    return cleaned[:180]


def object_key(*, config: Config, user_id: str, session_id: str, file_id: str, filename: str) -> str:
    parts: List[str] = []
    prefix = (getattr(config.cos, "path_prefix", None) or "").replace("\\", "/").strip().strip("/")
    for seg in prefix.split("/"):
        if not seg.strip():
            continue
        parts.append(_safe_seg(seg))
    uid = _safe_seg(user_id or "anonymous")
    sid = _safe_seg(session_id)
    fid = _safe_seg(file_id)
    parts.extend([uid, sid, fid, safe_filename(filename)])
    return "/".join(parts)


def _safe_seg(value: str) -> str:
    text = (value or "").replace("\\", "/").strip("/")
    text = _UNSAFE_NAME.sub("_", text).strip("._") or "x"
    return text[:80]


def mime_allowed(mime: str, allow: Iterable[str]) -> bool:
    patterns = [str(p).strip().lower() for p in (allow or []) if str(p).strip()]
    if not patterns:
        return True
    mime_l = (mime or "").strip().lower() or "application/octet-stream"
    for pat in patterns:
        if pat in ("*", "*/*"):
            return True
        if pat.endswith("/*"):
            if mime_l.startswith(pat[:-1]):
                return True
        elif mime_l == pat:
            return True
    return False


def _expires_at(seconds: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def record_files(rec) -> List[Dict[str, Any]]:
    meta = dict(getattr(rec, "metadata", None) or {})
    raw = meta.get("files") or []
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)]


def save_record_files(store, rec, files: List[Dict[str, Any]]) -> None:
    rec.metadata = dict(rec.metadata or {})
    rec.metadata["files"] = files
    store.update_session(rec)


def session_files(session) -> List[Dict[str, Any]]:
    store = getattr(session, "store", None)
    sid = getattr(session, "id", "") or ""
    if store is not None and sid and hasattr(store, "get_session"):
        rec = store.get_session(sid)
        if rec is not None:
            return record_files(rec)
    return [dict(x) for x in (getattr(session, "_files", None) or []) if isinstance(x, dict)]


def write_session_files(session, files: List[Dict[str, Any]]) -> None:
    session._files = list(files)
    store = getattr(session, "store", None)
    sid = getattr(session, "id", "") or ""
    if store is None or not sid or not hasattr(store, "get_session"):
        return
    rec = store.get_session(sid)
    if rec is None:
        return
    save_record_files(store, rec, files)


def get_file(files: List[Dict[str, Any]], file_id: str) -> Optional[Dict[str, Any]]:
    fid = (file_id or "").strip()
    for item in files:
        if str(item.get("id") or "") == fid:
            return item
    return None


def ready_files(files: List[Dict[str, Any]], *, file_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    ready = [f for f in files if str(f.get("status") or "") == "ready"]
    if file_ids is None:
        return ready
    wanted = {str(x).strip() for x in file_ids if str(x).strip()}
    return [f for f in ready if str(f.get("id") or "") in wanted]


def public_file(
    rec,
    item: Dict[str, Any],
    *,
    include_pending: bool = False,
) -> Optional[Dict[str, Any]]:
    status = str(item.get("status") or "")
    if status != "ready" and not include_pending:
        return None
    fid = str(item.get("id") or "")
    if not fid:
        return None
    sid = str(getattr(rec, "id", "") or "")
    return {
        "id": fid,
        "filename": str(item.get("filename") or ""),
        "mime": str(item.get("mime") or ""),
        "size": int(item.get("size") or 0),
        "role": str(item.get("role") or "user"),
        "status": status,
        "download_url": f"/v1/sessions/{sid}/files/{fid}",
    }


def public_files(rec, *, include_pending: bool = False, file_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    items = record_files(rec)
    if file_ids is not None:
        wanted = {str(x).strip() for x in file_ids if str(x).strip()}
        items = [f for f in items if str(f.get("id") or "") in wanted]
    out: List[Dict[str, Any]] = []
    for item in items:
        row = public_file(rec, item, include_pending=include_pending)
        if row is not None:
            out.append(row)
    return out


def validate_upload(*, config: Config, rec, filename: str, mime: str, size: int) -> None:
    fcfg = files_config(config)
    max_bytes = int(fcfg.max_bytes or 0)
    max_count = int(fcfg.max_count or 0)
    if size < 0:
        raise MailboxError("size must be >= 0")
    if max_bytes and size > max_bytes:
        raise MailboxError(f"file too large: {size} > {max_bytes}", 413)
    if not safe_filename(filename):
        raise MailboxError("filename required")
    if not mime_allowed(mime, fcfg.mime_allow):
        raise MailboxError(f"mime not allowed: {mime or '(empty)'}")
    existing = record_files(rec)
    if max_count and len(existing) >= max_count:
        raise MailboxError(f"too many files in session (max {max_count})", 413)


def create_upload(
    *,
    config: Config,
    store,
    rec,
    filename: str,
    mime: str,
    size: int,
    object_store: Optional[ObjectStore] = None,
) -> Dict[str, Any]:
    validate_upload(config=config, rec=rec, filename=filename, mime=mime, size=size)
    store_impl = object_store or object_store_from_config(config)
    fid = new_file_id()
    key = object_key(
        config=config,
        user_id=str(getattr(rec, "user_id", "") or ""),
        session_id=str(rec.id),
        file_id=fid,
        filename=filename,
    )
    fcfg = files_config(config)
    expires = int(fcfg.presign_put_expires or 900)
    upload_url = store_impl.presign_put(key=key, mime=mime or "", expires=expires)
    item = {
        "id": fid,
        "role": "user",
        "filename": safe_filename(filename),
        "mime": (mime or "").strip() or "application/octet-stream",
        "size": int(size),
        "object_key": key,
        "status": "pending",
    }
    files = record_files(rec)
    files.append(item)
    save_record_files(store, rec, files)
    return {
        "file_id": fid,
        "object_key": key,
        "upload_url": upload_url,
        "expires_at": _expires_at(expires),
        "headers": {"Content-Type": item["mime"]} if mime else {},
    }


def complete_upload(
    *,
    config: Config,
    store,
    rec,
    file_id: str,
    object_store: Optional[ObjectStore] = None,
) -> Dict[str, Any]:
    files = record_files(rec)
    item = get_file(files, file_id)
    if item is None:
        raise MailboxError("file not found")
    if str(item.get("status") or "") == "ready":
        return item
    store_impl = object_store or object_store_from_config(config)
    key = str(item.get("object_key") or "")
    if not key:
        raise MailboxError("file is missing object_key")
    head = store_impl.head(key)
    if head is None:
        raise MailboxError("object not found; upload the file to upload_url first")
    size = int(head.get("size") or 0)
    fcfg = files_config(config)
    max_bytes = int(fcfg.max_bytes or 0)
    if max_bytes and size > max_bytes:
        raise MailboxError(f"uploaded object too large: {size} > {max_bytes}", 413)
    claimed = int(item.get("size") or 0)
    if claimed and size and size != claimed:
        # Trust the stored object; keep claimed only as a hint.
        item["size"] = size
    elif size:
        item["size"] = size
    if head.get("mime") and not item.get("mime"):
        item["mime"] = str(head.get("mime") or item.get("mime") or "")
    item["status"] = "ready"
    save_record_files(store, rec, files)
    return item


def download_target(
    *,
    config: Config,
    rec,
    file_id: str,
    object_store: Optional[ObjectStore] = None,
) -> str:
    item = get_file(record_files(rec), file_id)
    if item is None or str(item.get("status") or "") != "ready":
        raise MailboxError("file not found")
    ext = str(item.get("external_url") or "").strip()
    if _https_url(ext) and not str(item.get("object_key") or "").strip():
        return ext
    key = str(item.get("object_key") or "").strip()
    if not key:
        if _https_url(ext):
            return ext
        raise MailboxError("file has no object_key")
    store_impl = object_store or object_store_from_config(config)
    fcfg = files_config(config)
    expires = int(fcfg.presign_get_expires or 300)
    return store_impl.presign_get(
        key=key,
        mime=str(item.get("mime") or ""),
        expires=expires,
    )


def _https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("https",) and bool(parsed.netloc)


def attachment_refs(
    *,
    config: Config,
    session,
    file_ids: Optional[List[str]] = None,
    object_store: Optional[ObjectStore] = None,
) -> List[Dict[str, Any]]:
    files = ready_files(session_files(session), file_ids=file_ids)
    if not files:
        return []
    try:
        store_impl = object_store or object_store_from_config(config)
    except CosNotConfigured:
        store_impl = None
    fcfg = files_config(config)
    expires = int(fcfg.presign_get_expires or 300)
    refs: List[Dict[str, Any]] = []
    for item in files:
        key = str(item.get("object_key") or "")
        url = str(item.get("external_url") or "")
        if store_impl is not None and key:
            try:
                url = store_impl.presign_get(
                    key=key,
                    mime=str(item.get("mime") or ""),
                    expires=expires,
                )
            except CosError:
                url = url if _https_url(url) else ""
        elif not _https_url(url):
            url = ""
        if not url:
            continue
        refs.append(
            {
                "file_id": str(item.get("id") or ""),
                "filename": str(item.get("filename") or ""),
                "mime": str(item.get("mime") or ""),
                "size": int(item.get("size") or 0),
                "object_key": key,
                "url": url,
            }
        )
    return refs


def files_prompt_block(session) -> str:
    files = ready_files(session_files(session), file_ids=getattr(session, "_prompt_file_ids", None))
    if not files:
        return ""
    lines = [
        "# Session files",
        "The user attached files to this session. They are not inlined in the prompt.",
        "MCP tools receive `attachment_refs_json` automatically. Use `kb_lookup` to search the knowledge base.",
        "To return a generated text file, call `save_output_file`. Do not embed file bytes or data-URLs.",
        "Attached:",
    ]
    for item in files:
        lines.append(
            f"- `{item.get('id')}` {item.get('filename')} ({item.get('mime')}, {item.get('size')} bytes)"
        )
    return "\n".join(lines)


def register_assistant_file(
    session,
    *,
    filename: str,
    mime: str,
    size: int,
    object_key: str = "",
    external_url: str = "",
    file_id: str = "",
) -> Dict[str, Any]:
    fid = (file_id or "").strip() or new_file_id()
    item = {
        "id": fid,
        "role": "assistant",
        "filename": safe_filename(filename),
        "mime": (mime or "").strip() or "application/octet-stream",
        "size": int(size or 0),
        "object_key": (object_key or "").strip(),
        "status": "ready",
    }
    if _https_url(external_url):
        item["external_url"] = external_url.strip()
    files = session_files(session)
    existing = get_file(files, fid)
    if existing is not None:
        existing.update(item)
    else:
        files.append(item)
    write_session_files(session, files)
    turn = list(getattr(session, "_turn_file_ids", None) or [])
    if fid not in turn:
        turn.append(fid)
    session._turn_file_ids = turn
    return item


def harvest_tool_files(session, payload: Any) -> List[Dict[str, Any]]:
    """Register MCP/tool JSON ``files[]`` as assistant mailbox entries."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("files")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("filename") or entry.get("name") or "output")
        mime = str(entry.get("mime") or entry.get("contentType") or "")
        key = str(entry.get("object_key") or entry.get("objectKey") or "")
        url = str(entry.get("url") or entry.get("download_url") or "")
        if url.startswith("data:") or url.startswith("file:"):
            url = ""
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if not key and not _https_url(url):
            continue
        item = register_assistant_file(
            session,
            filename=filename,
            mime=mime,
            size=size,
            object_key=key,
            external_url=url,
            file_id=str(entry.get("id") or entry.get("file_id") or ""),
        )
        href = url if _https_url(url) else f"/v1/sessions/{session.id}/files/{item['id']}"
        if not _https_url(href):
            href = ""
        att = {
            "type": "file",
            "mime": item["mime"],
            "filename": item["filename"],
            "url": href if _https_url(href) else "",
        }
        if att["url"]:
            out.append(att)
        else:
            out.append(
                {
                    "type": "file",
                    "mime": item["mime"],
                    "filename": item["filename"],
                    "url": f"/v1/sessions/{session.id}/files/{item['id']}",
                }
            )
    return out


def put_generated_text(
    *,
    session,
    filename: str,
    content: str,
    mime: str = "",
    object_store: Optional[ObjectStore] = None,
) -> Dict[str, Any]:
    config = session.config
    data = (content or "").encode("utf-8")
    fcfg = files_config(config)
    max_bytes = int(fcfg.max_bytes or 0)
    if max_bytes and len(data) > max_bytes:
        raise MailboxError(f"file too large: {len(data)} > {max_bytes}", 413)
    if not filename.strip():
        raise MailboxError("filename required")
    files = session_files(session)
    max_count = int(fcfg.max_count or 0)
    if max_count and len(files) >= max_count:
        raise MailboxError(f"too many files in session (max {max_count})", 413)
    store_impl = object_store or object_store_from_config(config)
    fid = new_file_id()
    key = object_key(
        config=config,
        user_id=str(getattr(session, "user_id", "") or ""),
        session_id=str(session.id),
        file_id=fid,
        filename=filename,
    )
    use_mime = (mime or "").strip() or "text/plain; charset=utf-8"
    store_impl.put_bytes(key=key, data=data, mime=use_mime)
    return register_assistant_file(
        session,
        filename=filename,
        mime=use_mime,
        size=len(data),
        object_key=key,
        file_id=fid,
    )
