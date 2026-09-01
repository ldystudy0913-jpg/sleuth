"""Read session-file knobs from Config. Defaults live on FilesConfig only."""
from __future__ import annotations

from typing import List, Optional

from ..config import Config, FilesConfig


def files_cfg(config: Optional[Config] = None) -> FilesConfig:
    if config is None:
        return FilesConfig()
    return getattr(config, "files", None) or FilesConfig()


def _text(config: Optional[Config], attr: str) -> str:
    raw = getattr(files_cfg(config), attr, None)
    if raw is None:
        raw = getattr(FilesConfig(), attr, "")
    return str(raw or "")


def _positive_int(config: Optional[Config], attr: str) -> int:
    try:
        n = int(getattr(files_cfg(config), attr, 0) or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    try:
        return int(getattr(FilesConfig(), attr) or 0)
    except (TypeError, ValueError):
        return 0


def csv_list(config: Optional[Config], attr: str) -> List[str]:
    return [p.strip() for p in _text(config, attr).split(",") if p.strip()]


def sm4_key(config: Optional[Config] = None) -> str:
    return _text(config, "sm4_key").strip()


def require_encrypt(config: Optional[Config] = None) -> bool:
    return bool(files_cfg(config).require_encrypt)


def enc_algo(config: Optional[Config] = None) -> str:
    return _text(config, "enc_algo").strip()


def cipher_mime(config: Optional[Config] = None) -> str:
    return _text(config, "cipher_mime").strip()


def default_mime(config: Optional[Config] = None) -> str:
    return _text(config, "default_mime").strip() or FilesConfig().default_mime


def fallback_filename(config: Optional[Config] = None) -> str:
    return _text(config, "fallback_filename").strip() or FilesConfig().fallback_filename


def anonymous_user_id(config: Optional[Config] = None) -> str:
    return _text(config, "anonymous_user_id").strip() or FilesConfig().anonymous_user_id


def filename_max_chars(config: Optional[Config] = None) -> int:
    return _positive_int(config, "filename_max_chars")


def object_key_seg_max_chars(config: Optional[Config] = None) -> int:
    return _positive_int(config, "object_key_seg_max_chars")


def harvest_filename(config: Optional[Config] = None) -> str:
    return _text(config, "harvest_filename").strip() or FilesConfig().harvest_filename


def allowed_url_schemes(config: Optional[Config] = None) -> List[str]:
    return [p.lower() for p in csv_list(config, "allowed_url_schemes")]


def blocked_url_prefixes(config: Optional[Config] = None) -> List[str]:
    return [p.lower() for p in csv_list(config, "blocked_url_prefixes")]


def mime_wildcard(config: Optional[Config] = None) -> List[str]:
    return [p.lower() for p in csv_list(config, "mime_wildcard")]


def upload_form_field(config: Optional[Config] = None) -> str:
    return _text(config, "upload_form_field").strip()


def upload_filename_field(config: Optional[Config] = None) -> str:
    return _text(config, "upload_filename_field").strip()


def upload_mime_field(config: Optional[Config] = None) -> str:
    return _text(config, "upload_mime_field").strip()


def download_path_template(config: Optional[Config] = None) -> str:
    return _text(config, "download_path_template").strip() or FilesConfig().download_path_template


def download_disposition(config: Optional[Config] = None) -> str:
    return _text(config, "download_disposition").strip() or FilesConfig().download_disposition


def inline_disposition(config: Optional[Config] = None) -> str:
    return _text(config, "inline_disposition").strip() or FilesConfig().inline_disposition


def inline_query_param(config: Optional[Config] = None) -> str:
    return _text(config, "inline_query_param").strip() or FilesConfig().inline_query_param


def inline_query_truthy(config: Optional[Config] = None) -> List[str]:
    return [p.lower() for p in csv_list(config, "inline_query_truthy")]


def query_is_truthy(config: Optional[Config], raw: str) -> bool:
    return (raw or "").strip().lower() in inline_query_truthy(config)


def generated_mime(config: Optional[Config] = None) -> str:
    return _text(config, "generated_mime").strip() or FilesConfig().generated_mime


def deprecated_presign_message(config: Optional[Config] = None) -> str:
    return _text(config, "deprecated_presign_message").strip()


def include_pending_query(config: Optional[Config] = None) -> str:
    return _text(config, "include_pending_query").strip() or FilesConfig().include_pending_query


def status_ready(config: Optional[Config] = None) -> str:
    return _text(config, "status_ready").strip() or FilesConfig().status_ready


def excerpt_pending(config: Optional[Config] = None) -> str:
    return _text(config, "excerpt_pending").strip() or FilesConfig().excerpt_pending


def excerpt_ok(config: Optional[Config] = None) -> str:
    return _text(config, "excerpt_ok").strip() or FilesConfig().excerpt_ok


def excerpt_skipped(config: Optional[Config] = None) -> str:
    return _text(config, "excerpt_skipped").strip() or FilesConfig().excerpt_skipped


def excerpt_done(config: Optional[Config] = None) -> tuple:
    return (excerpt_ok(config), excerpt_skipped(config))


def role_user(config: Optional[Config] = None) -> str:
    return _text(config, "role_user").strip() or FilesConfig().role_user


def role_assistant(config: Optional[Config] = None) -> str:
    return _text(config, "role_assistant").strip() or FilesConfig().role_assistant


def missing_upload_message(config: Optional[Config] = None) -> str:
    return _text(config, "missing_upload_message").strip() or FilesConfig().missing_upload_message


def err_file_not_found(config: Optional[Config] = None) -> str:
    return _text(config, "err_file_not_found").strip() or FilesConfig().err_file_not_found


def err_no_object_key(config: Optional[Config] = None) -> str:
    return _text(config, "err_no_object_key").strip() or FilesConfig().err_no_object_key


def err_sm4_key(config: Optional[Config] = None) -> str:
    return _text(config, "err_sm4_key").strip() or FilesConfig().err_sm4_key


def err_filename_required(config: Optional[Config] = None) -> str:
    return _text(config, "err_filename_required").strip() or FilesConfig().err_filename_required


def prompt_preamble(config: Optional[Config] = None) -> str:
    return _text(config, "prompt_preamble") or FilesConfig().prompt_preamble


def prompt_item_line(config: Optional[Config] = None) -> str:
    return _text(config, "prompt_item_line") or FilesConfig().prompt_item_line


def prompt_excerpt_prefix(config: Optional[Config] = None) -> str:
    return _text(config, "prompt_excerpt_prefix") or FilesConfig().prompt_excerpt_prefix


def prompt_truncated_mark(config: Optional[Config] = None) -> str:
    return _text(config, "prompt_truncated_mark") or FilesConfig().prompt_truncated_mark


def prompt_skipped_line(config: Optional[Config] = None) -> str:
    return _text(config, "prompt_skipped_line") or FilesConfig().prompt_skipped_line


def prompt_pending_line(config: Optional[Config] = None) -> str:
    return _text(config, "prompt_pending_line") or FilesConfig().prompt_pending_line


def file_download_url(config: Optional[Config], session_id: str, file_id: str) -> str:
    return download_path_template(config).format(session_id=session_id, file_id=file_id)
