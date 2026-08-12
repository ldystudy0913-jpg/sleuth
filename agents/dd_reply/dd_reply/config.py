"""配置：全部来自环境变量 DD_REPLY_*。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_path(name: str) -> Optional[Path]:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw)


def _load_dotenv() -> None:
    """轻量加载 cwd/.env（仅填充尚未设置的键）。"""
    path = Path.cwd() / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


class Settings:
    def __init__(self, **overrides: object) -> None:
        _load_dotenv()
        self.mcp_host: str = str(
            overrides.get("mcp_host", _env("DD_REPLY_MCP_HOST", "127.0.0.1") or "127.0.0.1")
        )
        self.mcp_port: int = int(
            overrides.get("mcp_port", _env_int("DD_REPLY_MCP_PORT", 8792))
        )
        kb = overrides.get("kb_path", _env_path("DD_REPLY_KB_PATH"))
        self.kb_path: Optional[Path] = Path(kb) if kb else None

        # Remote knowledge-base search (production). Only "question" is required by API;
        # optional fields (knowledgeId etc.) can be sent via env.
        self.kb_api_url: str = str(
            overrides.get("kb_api_url", _env("DD_REPLY_KB_API_URL", "") or "")
        ).strip()
        self.kb_api_token: str = str(
            overrides.get("kb_api_token", _env("DD_REPLY_KB_API_TOKEN", "") or "")
        ).strip()
        self.kb_api_timeout: float = float(
            overrides.get(
                "kb_api_timeout",
                _env("DD_REPLY_KB_API_TIMEOUT", "30") or "30",
            )
        )
        # Authorization header value prefix; empty = send raw token as header value.
        self.kb_api_auth_scheme: str = str(
            overrides.get(
                "kb_api_auth_scheme",
                _env("DD_REPLY_KB_API_AUTH_SCHEME", "Bearer") or "Bearer",
            )
        ).strip()
        self.kb_api_auth_header: str = str(
            overrides.get(
                "kb_api_auth_header",
                _env("DD_REPLY_KB_API_AUTH_HEADER", "Authorization") or "Authorization",
            )
        ).strip() or "Authorization"
        # Optional body fields merged into every search request (JSON object string).
        self.kb_api_extra_body: str = str(
            overrides.get(
                "kb_api_extra_body",
                _env("DD_REPLY_KB_API_EXTRA_BODY", "") or "",
            )
        ).strip()
        # Convenience: single optional knowledgeId (also settable via EXTRA_BODY).
        self.kb_knowledge_id: str = str(
            overrides.get(
                "kb_knowledge_id",
                _env("DD_REPLY_KB_KNOWLEDGE_ID", "") or "",
            )
        ).strip()
        # When remote search fails / empty: use local risk_points.json (default off).
        self.kb_fallback_local: bool = bool(
            overrides.get(
                "kb_fallback_local",
                (_env("DD_REPLY_KB_FALLBACK_LOCAL", "0") or "0").strip().lower()
                in {"1", "true", "yes", "on"},
            )
        )

        self.llm_base_url: str = str(
            overrides.get("llm_base_url", _env("DD_REPLY_LLM_BASE_URL", "") or "")
        )
        self.llm_api_key: str = str(
            overrides.get("llm_api_key", _env("DD_REPLY_LLM_API_KEY", "") or "")
        )
        self.llm_model: str = str(
            overrides.get("llm_model", _env("DD_REPLY_LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini")
        )

        self.attachment_max_bytes: int = int(
            overrides.get(
                "attachment_max_bytes",
                _env_int("DD_REPLY_ATTACHMENT_MAX_BYTES", 52_428_800),
            )
        )
        self.attachment_max_files: int = int(
            overrides.get(
                "attachment_max_files",
                _env_int("DD_REPLY_ATTACHMENT_MAX_FILES", 20),
            )
        )
        self.attachment_excerpt_max_chars: int = int(
            overrides.get(
                "attachment_excerpt_max_chars",
                _env_int("DD_REPLY_ATTACHMENT_EXCERPT_MAX_CHARS", 8000),
            )
        )

        # Optional COS / MySQL (production attachments)
        self.sm4_key: str = str(
            overrides.get("sm4_key", _env("DD_REPLY_ECS_EMODE_B_KEY", "") or "")
        )
        self.mysql_host: str = str(
            overrides.get("mysql_host", _env("DD_REPLY_MYSQL_HOST", "") or "")
        )
        self.mysql_port: int = int(
            overrides.get("mysql_port", _env_int("DD_REPLY_MYSQL_PORT", 3306))
        )
        self.mysql_user: str = str(
            overrides.get("mysql_user", _env("DD_REPLY_MYSQL_USER", "") or "")
        )
        self.mysql_password: str = str(
            overrides.get("mysql_password", _env("DD_REPLY_MYSQL_PASSWORD", "") or "")
        )
        self.mysql_database: str = str(
            overrides.get("mysql_database", _env("DD_REPLY_MYSQL_DATABASE", "") or "")
        )
        self.mysql_ddp_file_table: str = str(
            overrides.get(
                "mysql_ddp_file_table",
                _env("DD_REPLY_MYSQL_DDP_FILE_TABLE", "ddp_file") or "ddp_file",
            )
        )
        self.cos_secret_id: str = str(
            overrides.get("cos_secret_id", _env("DD_REPLY_COS_SECRET_ID", "") or "")
        )
        self.cos_secret_key: str = str(
            overrides.get("cos_secret_key", _env("DD_REPLY_COS_SECRET_KEY", "") or "")
        )
        self.cos_region: str = str(
            overrides.get("cos_region", _env("DD_REPLY_COS_REGION", "") or "")
        )
        self.cos_bucket: str = str(
            overrides.get("cos_bucket", _env("DD_REPLY_COS_BUCKET", "") or "")
        )
        self.cos_endpoint: str = str(
            overrides.get("cos_endpoint", _env("DD_REPLY_COS_ENDPOINT", "") or "")
        )
        self.cos_path_prefix: str = str(
            overrides.get("cos_path_prefix", _env("DD_REPLY_COS_PATH_PREFIX", "") or "")
        )

    def cos_configured(self) -> bool:
        return bool(
            self.cos_secret_id
            and self.cos_secret_key
            and self.cos_bucket
            and (self.cos_region or self.cos_endpoint)
        )

    def mysql_configured(self) -> bool:
        return bool(self.mysql_host and self.mysql_user and self.mysql_database)

    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key)

    def kb_api_configured(self) -> bool:
        """Production remote KB: URL is enough; token optional for open intranet APIs."""
        return bool(self.kb_api_url)


_SETTINGS: Optional[Settings] = None


def get_settings(**overrides: object) -> Settings:
    global _SETTINGS
    if overrides:
        return Settings(**overrides)
    if _SETTINGS is None:
        _SETTINGS = Settings()
    return _SETTINGS
