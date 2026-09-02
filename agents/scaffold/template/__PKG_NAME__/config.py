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
        self.mcp_host: str = str(
            overrides.get(
                "mcp_host",
                _env("__ENV_PREFIX___MCP_HOST", "127.0.0.1") or "127.0.0.1",
            )
        )
        self.mcp_port: int = int(
            overrides.get("mcp_port", _env_int("__ENV_PREFIX___MCP_PORT", __MCP_PORT__))
        )
        self.service_name: str = "__PKG_NAME__-tools"
        __OPTIONAL_SETTINGS_INIT__

    def as_health(self) -> dict:
        return {
            "ok": True,
            "service": self.service_name,
            "agent_card": True,
        }


def get_settings() -> Settings:
    return Settings()
