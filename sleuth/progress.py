"""Session / SSE progress helpers. Event names come from FilesConfig."""
from __future__ import annotations

from typing import Any, Optional

from .config import Config, FilesConfig


def files_cfg(config: Optional[Config] = None) -> FilesConfig:
    if config is None:
        return FilesConfig()
    return getattr(config, "files", None) or FilesConfig()


def emit_ack(target: Any, **extra: Any) -> None:
    renderer = getattr(target, "renderer", None) or target
    fn = getattr(renderer, "on_ack", None)
    if not callable(fn):
        return
    cfg = files_cfg(getattr(target, "config", None))
    payload = {"type": cfg.ack_event_type or "ack"}
    payload.update(extra)
    try:
        fn(**payload)
    except TypeError:
        fn()


def emit_progress(
    target: Any,
    *,
    stage: str,
    file_id: str = "",
    page: Any = None,
    pages: Any = None,
    detail: str = "",
    **extra: Any,
) -> None:
    renderer = getattr(target, "renderer", None) or target
    fn = getattr(renderer, "on_progress", None)
    if not callable(fn):
        return
    cfg = files_cfg(getattr(target, "config", None))
    payload: dict = {
        "type": cfg.progress_event_type or "progress",
        "stage": stage,
    }
    if file_id:
        payload["file_id"] = file_id
    if page is not None:
        payload["page"] = page
    if pages is not None:
        payload["pages"] = pages
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    try:
        fn(**payload)
    except TypeError:
        fn(payload)
