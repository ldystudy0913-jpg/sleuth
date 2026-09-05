"""Public upload-limit payload from FilesConfig (frontend hint / intercept)."""
from __future__ import annotations

from typing import Any, Dict, List

from ..config import Config, FilesConfig
from . import settings as file_settings


def _csv(raw: str) -> List[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def mime_unrestricted(config: Config | None = None) -> bool:
    fcfg = file_settings.files_cfg(config)
    allow = [str(x).strip() for x in (fcfg.mime_allow or []) if str(x).strip()]
    if not allow:
        return True
    wild = set(_csv(fcfg.mime_wildcard or FilesConfig().mime_wildcard))
    return any(item in wild for item in allow)


def files_limits_payload(config: Config | None = None) -> Dict[str, Any]:
    cfg = config or Config()
    fcfg = file_settings.files_cfg(cfg)
    return {
        "max_bytes": int(fcfg.max_bytes or 0),
        "max_count": int(fcfg.max_count or 0),
        "mime_allow": list(fcfg.mime_allow or []),
        "mime_unrestricted": mime_unrestricted(cfg),
        "filename_max_chars": int(fcfg.filename_max_chars or 0),
        "upload_form_field": str(fcfg.upload_form_field or ""),
        "upload_filename_field": str(fcfg.upload_filename_field or ""),
        "upload_mime_field": str(fcfg.upload_mime_field or ""),
        "require_encrypt": bool(fcfg.require_encrypt),
        "image_exts": _csv(fcfg.image_exts),
        "pdf_exts": _csv(fcfg.pdf_exts),
        "xlsx_exts": _csv(fcfg.xlsx_exts),
        "xls_exts": _csv(fcfg.xls_exts),
        "docx_exts": _csv(fcfg.docx_exts),
        "text_exts": _csv(fcfg.text_exts),
    }
