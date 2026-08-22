"""附件加载：本地路径优先；可选 invest_id → MySQL meta + COS（未配置则跳过）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings


@dataclass
class AttachmentExcerpt:
    source: str
    text: str
    truncated: bool = False


@dataclass
class AttachmentBundle:
    excerpts: List[AttachmentExcerpt] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def _decode_text(data: bytes, max_chars: int) -> tuple[str, bool]:
    text = None
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = ""
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return text, truncated


def load_local_paths(
    paths: List[str],
    settings: Settings,
) -> AttachmentBundle:
    bundle = AttachmentBundle()
    max_files = settings.attachment_max_files
    max_bytes = settings.attachment_max_bytes
    max_chars = settings.attachment_excerpt_max_chars
    for i, raw in enumerate(paths or []):
        if i >= max_files:
            bundle.skipped.append(f"local:{raw}:exceeded max files {max_files}")
            continue
        path = Path(raw)
        if not path.is_file():
            bundle.skipped.append(f"local:{raw}:not found")
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            bundle.skipped.append(f"local:{raw}:{exc}")
            continue
        if size > max_bytes:
            bundle.skipped.append(f"local:{raw}:size {size} > {max_bytes}")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            bundle.skipped.append(f"local:{raw}:{exc}")
            continue
        if b"\x00" in data[:4096]:
            bundle.skipped.append(
                f"local:{raw}:binary skipped (use text export for tests)"
            )
            continue
        text, truncated = _decode_text(data, max_chars)
        if not text.strip():
            bundle.skipped.append(f"local:{raw}:empty text")
            continue
        bundle.excerpts.append(
            AttachmentExcerpt(source=str(path), text=text, truncated=truncated)
        )
    return bundle


def load_from_urls(
    refs: List[dict],
    settings: Settings,
    *,
    client: Any = None,
) -> AttachmentBundle:
    """Stream-download session mailbox refs (presigned https). Skip data:/file:."""
    bundle = AttachmentBundle()
    max_files = settings.attachment_max_files
    max_bytes = settings.attachment_max_bytes
    max_chars = settings.attachment_excerpt_max_chars
    owns = client is None
    http = client
    if owns:
        try:
            import httpx
        except ImportError:
            bundle.skipped.append("url:httpx not installed")
            return bundle
        http = httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        for i, ref in enumerate(refs or []):
            if i >= max_files:
                bundle.skipped.append(f"url:exceeded max files {max_files}")
                break
            if not isinstance(ref, dict):
                continue
            url = str(ref.get("url") or "").strip()
            name = str(ref.get("filename") or ref.get("file_id") or url or "attachment")
            if not url:
                bundle.skipped.append(f"url:{name}:missing url")
                continue
            if url.startswith("data:") or url.startswith("file:"):
                bundle.skipped.append(f"url:{name}:data/file URLs are not allowed")
                continue
            if not (url.startswith("https://") or url.startswith("http://")):
                bundle.skipped.append(f"url:{name}:not an http(s) URL")
                continue
            claimed = ref.get("size")
            try:
                claimed_i = int(claimed) if claimed is not None else 0
            except (TypeError, ValueError):
                claimed_i = 0
            if claimed_i and claimed_i > max_bytes:
                bundle.skipped.append(f"url:{name}:size {claimed_i} > {max_bytes}")
                continue
            try:
                with http.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        bundle.skipped.append(f"url:{name}:HTTP {resp.status_code}")
                        continue
                    cl = resp.headers.get("content-length")
                    try:
                        cl_i = int(cl) if cl else 0
                    except (TypeError, ValueError):
                        cl_i = 0
                    if cl_i and cl_i > max_bytes:
                        bundle.skipped.append(f"url:{name}:size {cl_i} > {max_bytes}")
                        continue
                    buf = bytearray()
                    truncated_bytes = False
                    for chunk in resp.iter_bytes(256 * 1024):
                        if not chunk:
                            continue
                        room = max_bytes - len(buf)
                        if room <= 0:
                            truncated_bytes = True
                            break
                        buf.extend(chunk[:room])
                        if len(chunk) > room:
                            truncated_bytes = True
                            break
            except Exception as exc:  # noqa: BLE001
                bundle.skipped.append(f"url:{name}:{exc}")
                continue
            data = bytes(buf)
            if truncated_bytes and len(data) >= max_bytes:
                bundle.skipped.append(f"url:{name}:size > {max_bytes}")
                continue
            if b"\x00" in data[:4096]:
                bundle.skipped.append(
                    f"url:{name}:binary skipped (use text export for tests)"
                )
                continue
            text, truncated = _decode_text(data, max_chars)
            if not text.strip():
                bundle.skipped.append(f"url:{name}:empty text")
                continue
            bundle.excerpts.append(
                AttachmentExcerpt(source=name, text=text, truncated=truncated)
            )
    finally:
        if owns and http is not None:
            try:
                http.close()
            except Exception:  # noqa: BLE001
                pass
    return bundle


def load_cos_by_invest_id(invest_id: str, settings: Settings) -> AttachmentBundle:
    """生产路径：尝试复用 dd_check AttachmentPipeline；缺依赖/配置则 skipped。"""
    bundle = AttachmentBundle()
    if not invest_id.strip():
        return bundle
    if not settings.mysql_configured():
        bundle.skipped.append("cos:mysql not configured")
        return bundle
    if not settings.cos_configured():
        bundle.skipped.append("cos:object store not configured")
        return bundle
    if not settings.sm4_key:
        bundle.skipped.append("cos:SM4 key not configured")
        return bundle
    try:
        from dd_check.attachments import AttachmentPipeline  # type: ignore
        from dd_check.attachments.cos_client import CosObjectStore  # type: ignore
        from dd_check.attachments.mysql_meta import MysqlFileMetaStore  # type: ignore
        from dd_check.config import Settings as DdSettings  # type: ignore
    except ImportError:
        bundle.skipped.append(
            "cos:install dd-analyst-capability[all] alongside dd-reply to enable COS; "
            "tests should use local_paths"
        )
        return bundle

    try:
        dd = DdSettings()
        # Best-effort: copy env-compatible fields if present on DdSettings
        for src, dst in (
            ("sm4_key", "ecs_emode_b_key"),
            ("attachment_max_bytes", "attachment_max_bytes"),
            ("attachment_max_files", "attachment_max_files"),
            ("attachment_excerpt_max_chars", "attachment_excerpt_max_chars"),
        ):
            if hasattr(dd, dst) and hasattr(settings, src):
                try:
                    setattr(dd, dst, getattr(settings, src))
                except Exception:  # noqa: BLE001
                    pass
        meta = MysqlFileMetaStore(dd)
        obj = CosObjectStore(dd)
        pipe = AttachmentPipeline(dd, meta_store=meta, object_store=obj)
        other = pipe.run(invest_id)
        for ex in getattr(other, "excerpts", []) or []:
            bundle.excerpts.append(
                AttachmentExcerpt(
                    source=getattr(ex, "location_path", invest_id),
                    text=getattr(ex, "text", ""),
                    truncated=bool(getattr(ex, "truncated", False)),
                )
            )
        for s in getattr(other, "skipped", []) or []:
            bundle.skipped.append(str(s))
    except Exception as exc:  # noqa: BLE001
        bundle.skipped.append(f"cos:fetch failed: {exc}")
    return bundle


def load_attachments(
    *,
    local_paths: Optional[List[str]] = None,
    invest_id: str = "",
    attachment_refs: Optional[List[dict]] = None,
    settings: Optional[Settings] = None,
) -> AttachmentBundle:
    from ..config import get_settings

    settings = settings or get_settings()
    merged = AttachmentBundle()
    local = load_local_paths(list(local_paths or []), settings)
    merged.excerpts.extend(local.excerpts)
    merged.skipped.extend(local.skipped)
    urls = load_from_urls(list(attachment_refs or []), settings)
    merged.excerpts.extend(urls.excerpts)
    merged.skipped.extend(urls.skipped)
    if invest_id.strip():
        remote = load_cos_by_invest_id(invest_id, settings)
        merged.excerpts.extend(remote.excerpts)
        merged.skipped.extend(remote.skipped)
    return merged
