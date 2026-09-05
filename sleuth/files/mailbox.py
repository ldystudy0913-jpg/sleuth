"""Session file mailbox: metadata in session.metadata.files, bytes in COS."""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from ..config import Config
from ..util.ids import file_id as new_file_id
from .cos import ObjectStore, object_store_from_config
from ..bizerror import BizErrorCode
from .errors import MailboxError
from . import at_rest
from . import settings

_UNSAFE_NAME = re.compile(r"[^\w.\-()+\[\]\u4e00-\u9fff]+", re.UNICODE)


def files_config(config: Config):
    return settings.files_cfg(config)


def safe_filename(name: str, config=None) -> str:
    raw = (name or "").replace("\\", "/").split("/")[-1].strip()
    raw = raw.lstrip(".")
    fallback = settings.fallback_filename(config)
    cleaned = _UNSAFE_NAME.sub("_", raw).strip("._") or fallback
    limit = settings.filename_max_chars(config)
    return cleaned[:limit] if limit else cleaned


def object_key(*, config: Config, user_id: str, session_id: str, file_id: str, filename: str) -> str:
    parts: List[str] = []
    prefix = (getattr(config.cos, "path_prefix", None) or "").replace("\\", "/").strip().strip("/")
    for seg in prefix.split("/"):
        if not seg.strip():
            continue
        parts.append(_safe_seg(seg, config))
    uid = _safe_seg(user_id or settings.anonymous_user_id(config), config)
    sid = _safe_seg(session_id, config)
    fid = _safe_seg(file_id, config)
    parts.extend([uid, sid, fid, safe_filename(filename, config)])
    return "/".join(parts)


def _safe_seg(value: str, config=None) -> str:
    text = (value or "").replace("\\", "/").strip("/")
    fallback = settings.fallback_filename(config)
    text = _UNSAFE_NAME.sub("_", text).strip("._") or fallback
    limit = settings.object_key_seg_max_chars(config)
    return text[:limit] if limit else text


def mime_allowed(mime: str, allow: Iterable[str], config=None) -> bool:
    patterns = [str(p).strip().lower() for p in (allow or []) if str(p).strip()]
    if not patterns:
        return True
    mime_l = (mime or "").strip().lower() or settings.default_mime(config)
    wild = set(settings.mime_wildcard(config))
    for pat in patterns:
        if pat in wild:
            return True
        if pat.endswith("/*"):
            if mime_l.startswith(pat[:-1]):
                return True
        elif mime_l == pat:
            return True
    return False


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


def ready_files(
    files: List[Dict[str, Any]],
    *,
    file_ids: Optional[List[str]] = None,
    config=None,
) -> List[Dict[str, Any]]:
    want = settings.status_ready(config)
    ready = [f for f in files if str(f.get("status") or "") == want]
    if file_ids is None:
        return ready
    wanted = {str(x).strip() for x in file_ids if str(x).strip()}
    return [f for f in ready if str(f.get("id") or "") in wanted]


def public_file(
    rec,
    item: Dict[str, Any],
    *,
    include_pending: bool = False,
    config=None,
) -> Optional[Dict[str, Any]]:
    status = str(item.get("status") or "")
    cfg = config or getattr(rec, "config", None)
    if status != settings.status_ready(cfg) and not include_pending:
        return None
    fid = str(item.get("id") or "")
    if not fid:
        return None
    sid = str(getattr(rec, "id", "") or "")
    href = str(item.get("download_url") or "").strip()
    if not href:
        href = settings.file_download_url(cfg, sid, fid)
    row = {
        "id": fid,
        "filename": str(item.get("filename") or ""),
        "mime": str(item.get("mime") or ""),
        "size": int(item.get("size") or 0),
        "role": str(item.get("role") or settings.role_user(cfg)),
        "status": status,
        "download_url": href,
    }
    excerpt_status = str(item.get("excerpt_status") or "")
    if excerpt_status:
        row["excerpt_status"] = excerpt_status
    if item.get("encrypted"):
        row["encrypted"] = True
    excerpt = item.get("excerpt")
    if isinstance(excerpt, dict) and excerpt.get("skipped"):
        row["excerpt_skipped"] = str(excerpt.get("skipped") or "")
    return row


def public_files(
    rec,
    *,
    include_pending: bool = False,
    file_ids: Optional[List[str]] = None,
    config=None,
) -> List[Dict[str, Any]]:
    items = record_files(rec)
    if file_ids is not None:
        wanted = {str(x).strip() for x in file_ids if str(x).strip()}
        items = [f for f in items if str(f.get("id") or "") in wanted]
    out: List[Dict[str, Any]] = []
    for item in items:
        row = public_file(rec, item, include_pending=include_pending, config=config)
        if row is not None:
            out.append(row)
    return out


def validate_upload(*, config: Config, rec, filename: str, mime: str, size: int) -> None:
    fcfg = files_config(config)
    max_bytes = int(fcfg.max_bytes or 0)
    max_count = int(fcfg.max_count or 0)
    if size < 0:
        raise MailboxError(BizErrorCode.PARAM_INVALID, "size must be >= 0")
    if max_bytes and size > max_bytes:
        raise MailboxError(
            BizErrorCode.FILE_UPLOAD_FAILED,
            f"file too large: {size} > {max_bytes}",
            status=413,
        )
    if not safe_filename(filename, config):
        raise MailboxError(BizErrorCode.FILE_UPLOAD_FAILED, settings.err_filename_required(config))
    if not mime_allowed(mime, fcfg.mime_allow, config):
        raise MailboxError(BizErrorCode.FILE_UPLOAD_FAILED, f"mime not allowed: {mime or '(empty)'}")
    existing = record_files(rec)
    if max_count and len(existing) >= max_count:
        raise MailboxError(
            BizErrorCode.FILE_UPLOAD_FAILED,
            f"too many files in session (max {max_count})",
            status=413,
        )


def ingest_user_file(
    *,
    config: Config,
    store,
    rec,
    filename: str,
    mime: str,
    data: bytes,
    object_store: Optional[ObjectStore] = None,
) -> Dict[str, Any]:
    plain = data or b""
    validate_upload(config=config, rec=rec, filename=filename, mime=mime, size=len(plain))
    stored, encrypted = at_rest.store_payload(plain, config)
    store_impl = object_store or object_store_from_config(config)
    fid = new_file_id()
    key = object_key(
        config=config,
        user_id=str(getattr(rec, "user_id", "") or ""),
        session_id=str(rec.id),
        file_id=fid,
        filename=filename,
    )
    use_mime = (mime or "").strip() or settings.default_mime(config)
    store_impl.put_bytes(
        key=key,
        data=stored,
        mime=at_rest.put_mime(config, encrypted=encrypted, original_mime=use_mime),
    )
    item = _ready_item(
        config=config,
        rec=rec,
        file_id=fid,
        role=settings.role_user(config),
        filename=filename,
        mime=use_mime,
        size=len(plain),
        object_key=key,
        encrypted=encrypted,
    )
    files = record_files(rec)
    files.append(item)
    save_record_files(store, rec, files)
    _schedule_extract(
        config=config,
        store=store,
        session_id=str(rec.id),
        file_id=fid,
        object_store=store_impl,
    )
    return item


def _ready_item(
    *,
    config: Config,
    rec,
    file_id: str,
    role: str,
    filename: str,
    mime: str,
    size: int,
    object_key: str,
    encrypted: bool,
) -> Dict[str, Any]:
    item = {
        "id": file_id,
        "role": role,
        "filename": safe_filename(filename, config),
        "mime": mime,
        "size": int(size),
        "object_key": object_key,
        "status": settings.status_ready(config),
        "encrypted": encrypted,
        "excerpt_status": settings.excerpt_pending(config),
        "download_url": settings.file_download_url(config, str(rec.id), file_id),
    }
    if encrypted:
        item["enc"] = settings.enc_algo(config)
    return item


def open_plaintext(
    *,
    config: Config,
    rec,
    file_id: str,
    object_store: Optional[ObjectStore] = None,
) -> Tuple[Dict[str, Any], bytes]:
    item = get_file(record_files(rec), file_id)
    if item is None or str(item.get("status") or "") != settings.status_ready(config):
        raise MailboxError(BizErrorCode.READ_FAIL, file_id, status=404)
    key = str(item.get("object_key") or "").strip()
    if not key:
        raise MailboxError(BizErrorCode.READ_FAIL, settings.err_no_object_key(config))
    store_impl = object_store or object_store_from_config(config)
    fcfg = files_config(config)
    raw = store_impl.get_bytes(key, max_bytes=int(fcfg.max_bytes or 0))
    plain = at_rest.restore_plaintext(raw, encrypted=bool(item.get("encrypted")), config=config)
    return item, plain


def delete_session_file(
    *,
    config: Config,
    store,
    rec,
    file_id: str,
    object_store: Optional[ObjectStore] = None,
) -> None:
    files = record_files(rec)
    item = get_file(files, file_id)
    if item is None:
        raise MailboxError(BizErrorCode.READ_FAIL, file_id, status=404)
    key = str(item.get("object_key") or "").strip()
    kept = [f for f in files if str(f.get("id") or "") != file_id]
    save_record_files(store, rec, kept)
    if not key:
        return
    store_impl = object_store or object_store_from_config(config)
    delete_fn = getattr(store_impl, "delete_object", None)
    if callable(delete_fn):
        delete_fn(key)


def _schedule_extract(*, config, store, session_id: str, file_id: str, object_store=None) -> None:
    if not session_id or not file_id:
        return
    from .ingest import schedule_extract

    schedule_extract(
        config=config,
        store=store,
        session_id=session_id,
        file_id=file_id,
        object_store=object_store,
    )


def _href_allowed(url: str, config=None) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    schemes = set(settings.allowed_url_schemes(config))
    return parsed.scheme.lower() in schemes and bool(parsed.netloc)


def _url_blocked(url: str, config=None) -> bool:
    low = (url or "").strip().lower()
    for pfx in settings.blocked_url_prefixes(config):
        if low.startswith(pfx):
            return True
    return False


def attachment_refs(
    *,
    config: Config,
    session,
    file_ids: Optional[List[str]] = None,
    object_store: Optional[ObjectStore] = None,
) -> List[Dict[str, Any]]:
    files = ready_files(session_files(session), file_ids=file_ids, config=config)
    if not files:
        return []
    sid = str(getattr(session, "id", "") or "")
    refs: List[Dict[str, Any]] = []
    for item in files:
        fid = str(item.get("id") or "")
        excerpt = item.get("excerpt") if isinstance(item.get("excerpt"), dict) else {}
        excerpt_text = str((excerpt or {}).get("text") or "")
        url = str(item.get("download_url") or "").strip()
        if not url and sid and fid:
            url = settings.file_download_url(config, sid, fid)
        if not excerpt_text and not url:
            continue
        ref = {
            "file_id": fid,
            "filename": str(item.get("filename") or ""),
            "mime": str(item.get("mime") or ""),
            "size": int(item.get("size") or 0),
            "object_key": str(item.get("object_key") or ""),
            "url": url,
            "encrypted": bool(item.get("encrypted")),
            "excerpt_status": str(item.get("excerpt_status") or ""),
        }
        if excerpt_text:
            ref["excerpt"] = excerpt_text
            ref["truncated"] = bool((excerpt or {}).get("truncated"))
        skipped = str((excerpt or {}).get("skipped") or "")
        if skipped:
            ref["excerpt_skipped"] = skipped
        refs.append(ref)
    return refs


def files_prompt_block(session) -> str:
    cfg = getattr(session, "config", None)
    files = ready_files(
        session_files(session),
        file_ids=getattr(session, "_prompt_file_ids", None),
        config=cfg,
    )
    if not files:
        return ""
    lines = [settings.prompt_preamble(cfg)]
    pending = settings.excerpt_pending(cfg)
    item_tmpl = settings.prompt_item_line(cfg)
    excerpt_tmpl = settings.prompt_excerpt_prefix(cfg)
    skip_tmpl = settings.prompt_skipped_line(cfg)
    pending_line = settings.prompt_pending_line(cfg)
    truncated_mark = settings.prompt_truncated_mark(cfg)
    for item in files:
        status = str(item.get("excerpt_status") or pending)
        excerpt = item.get("excerpt") if isinstance(item.get("excerpt"), dict) else {}
        parser = str((excerpt or {}).get("parser") or "")
        lines.append(
            item_tmpl.format(
                id=item.get("id"),
                filename=item.get("filename"),
                mime=item.get("mime"),
                size=item.get("size"),
                status=status,
                parser=parser,
            )
        )
        text = str((excerpt or {}).get("text") or "")
        skipped = str((excerpt or {}).get("skipped") or "")
        if text:
            mark = truncated_mark if excerpt.get("truncated") else ""
            lines.append(excerpt_tmpl.format(mark=mark))
            lines.append(text)
        elif skipped:
            lines.append(skip_tmpl.format(skipped=skipped))
        elif status == pending:
            lines.append(pending_line)
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
    encrypted: bool = False,
) -> Dict[str, Any]:
    fid = (file_id or "").strip() or new_file_id()
    cfg = getattr(session, "config", None)
    item = {
        "id": fid,
        "role": settings.role_assistant(cfg),
        "filename": safe_filename(filename, cfg),
        "mime": (mime or "").strip() or settings.default_mime(cfg),
        "size": int(size or 0),
        "object_key": (object_key or "").strip(),
        "status": settings.status_ready(cfg),
        "encrypted": bool(encrypted),
        "download_url": settings.file_download_url(cfg, str(session.id), fid),
    }
    if encrypted:
        item["enc"] = settings.enc_algo(cfg)
    if _href_allowed(external_url, cfg):
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


def _entry_plain_bytes(entry: Dict[str, Any]) -> Optional[bytes]:
    raw_b64 = entry.get("content_base64")
    if raw_b64 is None:
        raw_b64 = entry.get("contentBase64")
    if isinstance(raw_b64, str) and raw_b64.strip():
        try:
            return base64.b64decode(raw_b64, validate=False)
        except Exception:
            return None
    content = entry.get("content")
    if isinstance(content, str) and content:
        return content.encode("utf-8")
    return None


def harvest_tool_files(session, payload: Any) -> List[Dict[str, Any]]:
    """Register MCP/tool JSON ``files[]`` as assistant mailbox entries."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("files")
    if not isinstance(raw, list):
        return []
    cfg = getattr(session, "config", None)
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        filename = str(
            entry.get("filename") or entry.get("name") or settings.harvest_filename(cfg)
        )
        mime = str(entry.get("mime") or entry.get("contentType") or "")
        key = str(entry.get("object_key") or entry.get("objectKey") or "")
        url = str(entry.get("url") or entry.get("download_url") or "")
        if _url_blocked(url, cfg):
            url = ""
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        body = _entry_plain_bytes(entry)
        if body is not None:
            try:
                item = put_generated_bytes(
                    session=session,
                    filename=filename,
                    data=body,
                    mime=mime,
                )
            except MailboxError:
                continue
            href = str(item.get("download_url") or "")
            if not href:
                href = settings.file_download_url(cfg, str(session.id), item["id"])
        else:
            if not key and not _href_allowed(url, cfg):
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
            href = url if _href_allowed(url, cfg) else item.get("download_url") or ""
            if not href:
                href = settings.file_download_url(cfg, str(session.id), item["id"])
        out.append(
            {
                "type": "file",
                "id": item["id"],
                "mime": item["mime"],
                "filename": item["filename"],
                "url": href,
            }
        )
    return out


def put_generated_bytes(
    *,
    session,
    filename: str,
    data: bytes,
    mime: str = "",
    object_store: Optional[ObjectStore] = None,
) -> Dict[str, Any]:
    config = session.config
    payload = bytes(data or b"")
    fcfg = files_config(config)
    max_bytes = int(fcfg.max_bytes or 0)
    if max_bytes and len(payload) > max_bytes:
        raise MailboxError(
            BizErrorCode.FILE_UPLOAD_FAILED,
            f"file too large: {len(payload)} > {max_bytes}",
            status=413,
        )
    if not filename.strip():
        raise MailboxError(BizErrorCode.FILE_UPLOAD_FAILED, settings.err_filename_required(config))
    files = session_files(session)
    max_count = int(fcfg.max_count or 0)
    if max_count and len(files) >= max_count:
        raise MailboxError(
            BizErrorCode.FILE_UPLOAD_FAILED,
            f"too many files in session (max {max_count})",
            status=413,
        )
    store_impl = object_store or object_store_from_config(config)
    fid = new_file_id()
    key = object_key(
        config=config,
        user_id=str(getattr(session, "user_id", "") or ""),
        session_id=str(session.id),
        file_id=fid,
        filename=filename,
    )
    use_mime = (mime or "").strip() or settings.generated_mime(config)
    stored, encrypted = at_rest.store_payload(payload, config)
    store_impl.put_bytes(
        key=key,
        data=stored,
        mime=at_rest.put_mime(config, encrypted=encrypted, original_mime=use_mime),
    )
    return register_assistant_file(
        session,
        filename=filename,
        mime=use_mime,
        size=len(payload),
        object_key=key,
        file_id=fid,
        encrypted=encrypted,
    )


def put_generated_text(
    *,
    session,
    filename: str,
    content: str,
    mime: str = "",
    object_store: Optional[ObjectStore] = None,
) -> Dict[str, Any]:
    return put_generated_bytes(
        session=session,
        filename=filename,
        data=(content or "").encode("utf-8"),
        mime=mime,
        object_store=object_store,
    )
