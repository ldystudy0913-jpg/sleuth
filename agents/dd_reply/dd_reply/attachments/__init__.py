"""附件加载：本地路径优先；可选 invest_id → MySQL meta + COS（未配置则跳过）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

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
    settings: Optional[Settings] = None,
) -> AttachmentBundle:
    from ..config import get_settings

    settings = settings or get_settings()
    merged = AttachmentBundle()
    local = load_local_paths(list(local_paths or []), settings)
    merged.excerpts.extend(local.excerpts)
    merged.skipped.extend(local.skipped)
    if invest_id.strip():
        remote = load_cos_by_invest_id(invest_id, settings)
        merged.excerpts.extend(remote.excerpts)
        merged.skipped.extend(remote.skipped)
    return merged
