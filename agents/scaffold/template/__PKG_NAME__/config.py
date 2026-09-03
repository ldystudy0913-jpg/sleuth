"""Settings from environment (__ENV_PREFIX___*). Do not put these in Sleuth .env."""
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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_truthy(name: str) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _load_dotenv() -> None:
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
        p = "__ENV_PREFIX__"
        self.mcp_host: str = str(
            overrides.get(
                "mcp_host",
                _env(f"{p}_MCP_HOST", "127.0.0.1") or "127.0.0.1",
            )
        )
        self.mcp_port: int = int(
            overrides.get("mcp_port", _env_int(f"{p}_MCP_PORT", __MCP_PORT__))
        )
        self.service_name: str = "__PKG_NAME__-tools"
        self.mcp_token: str = str(
            overrides.get("mcp_token", _env(f"{p}_MCP_TOKEN", "") or "")
        ).strip()
        self.attachments_enabled: bool = bool(
            overrides.get("attachments_enabled", _env_truthy(f"{p}_ATTACHMENTS"))
        )

        self.kb_api_url: str = str(
            overrides.get("kb_api_url", _env(f"{p}_KB_API_URL", "") or "")
        ).strip()
        self.kb_login_url: str = str(
            overrides.get("kb_login_url", _env(f"{p}_KB_LOGIN_URL", "") or "")
        ).strip()
        self.kb_openid: str = str(
            overrides.get("kb_openid", _env(f"{p}_KB_OPENID", "") or "")
        ).strip()
        self.kb_service_id: str = str(
            overrides.get("kb_service_id", _env(f"{p}_KB_SERVICEID", "") or "")
        ).strip()
        self.kb_api_timeout: float = float(
            overrides.get("kb_api_timeout", _env_float(f"{p}_KB_API_TIMEOUT", 30.0))
        )
        self.kb_sort_count: int = int(
            overrides.get("kb_sort_count", _env_int(f"{p}_KB_SORT_COUNT", 10))
        )
        self.kb_knowledge_ids: str = str(
            overrides.get("kb_knowledge_ids", _env(f"{p}_KB_KNOWLEDGE_IDS", "") or "")
        ).strip()
        self.kb_recall_count: int = int(
            overrides.get("kb_recall_count", _env_int(f"{p}_KB_RECALL_COUNT", 10))
        )

        self.cos_secret_id: str = str(
            overrides.get(
                "cos_secret_id",
                _env(f"{p}_AWS_ACCESS_KEY_ID", "")
                or _env(f"{p}_COS_SECRET_ID", "")
                or "",
            )
        ).strip()
        self.cos_secret_key: str = str(
            overrides.get(
                "cos_secret_key",
                _env(f"{p}_AWS_SECRET_ACCESS_KEY", "")
                or _env(f"{p}_COS_SECRET_KEY", "")
                or "",
            )
        ).strip()
        self.cos_region: str = str(
            overrides.get(
                "cos_region",
                _env(f"{p}_AWS_DEFAULT_REGION", "") or _env(f"{p}_COS_REGION", "") or "",
            )
        ).strip()
        self.cos_endpoint: str = str(
            overrides.get(
                "cos_endpoint",
                _env(f"{p}_S3_ENDPOINT", "") or _env(f"{p}_COS_ENDPOINT", "") or "",
            )
        ).strip()
        self.cos_bucket: str = str(
            overrides.get("cos_bucket", _env(f"{p}_COS_BUCKET", "") or "")
        ).strip()
        self.cos_path_prefix: str = str(
            overrides.get(
                "cos_path_prefix",
                _env(f"{p}_COS_PATH_PREFIX", "sleuth/files") or "sleuth/files",
            )
        ).strip()
        if "kb_enabled" in overrides:
            self.kb_enabled = bool(overrides.get("kb_enabled"))
        else:
            self.kb_enabled = bool(
                self.kb_api_url
                and self.kb_login_url
                and self.kb_openid
                and self.kb_service_id
            )
        if "output_enabled" in overrides:
            self.output_enabled = bool(overrides.get("output_enabled"))
        else:
            self.output_enabled = bool(
                self.cos_secret_id
                and self.cos_secret_key
                and self.cos_bucket
                and (self.cos_region or self.cos_endpoint)
            )

    def as_health(self) -> dict:
        return {
            "ok": True,
            "service": self.service_name,
            "agent_card": True,
            "attachments": self.attachments_enabled,
            "kb": self.kb_enabled,
            "output": self.output_enabled,
            "mcp_auth": bool(self.mcp_token),
        }


def get_settings() -> Settings:
    return Settings()
