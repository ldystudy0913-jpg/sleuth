"""Create a Store from Config / environment."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import Store
from .sqlite import SQLiteStore, default_db_path

if TYPE_CHECKING:
    from ..config import Config


def create_store(config: Optional["Config"] = None, *, backend: Optional[str] = None) -> Store:
    from ..config import Config as ConfigCls

    cfg = config or ConfigCls()
    kind = (backend or cfg.storage.backend or "sqlite").strip().lower()

    if kind == "mysql":
        from .mysql import MySQLStore

        password = cfg.storage.mysql_password
        if not password:
            env_name = cfg.storage.mysql_password_env or "SLEUTH_MYSQL_PASSWORD"
            password = os.environ.get(env_name) or os.environ.get("SLEUTH_MYSQL_PASSWORD") or ""
        return MySQLStore(
            host=cfg.storage.mysql_host,
            port=cfg.storage.mysql_port,
            user=cfg.storage.mysql_user,
            password=password,
            database=cfg.storage.mysql_database,
        )

    path = cfg.storage.sqlite_path
    db_path = Path(path) if path else default_db_path()
    return SQLiteStore(db_path)
