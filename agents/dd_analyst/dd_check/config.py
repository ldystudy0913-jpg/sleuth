"""配置：全部来自环境变量 DD_CHECK_*（密钥/端点不写死在代码里）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    """读字符串环境变量。"""
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_path(name: str) -> Optional[Path]:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw)


def _env_json_dict(name: str, default: Dict[str, Any]) -> Dict[str, Any]:
    raw = os.environ.get(name)
    if not raw:
        return dict(default)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object")
    return {str(k): v for k, v in data.items()}


def _env_json_list(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"{name} must be a JSON array")
    return [str(x) for x in data]


_DEFAULT_CUST_MAP = {
    "p": "PRIVATE",
    "private": "PRIVATE",
    "个人": "PRIVATE",
    "对私": "PRIVATE",
    "c": "CORPORATE",
    "corp": "CORPORATE",
    "corporate": "CORPORATE",
    "对公": "CORPORATE",
    "企业": "CORPORATE",
    "o": "OTHER",
    "other": "OTHER",
    "其他": "OTHER",
}

_DEFAULT_REGIONS = [
    "伊朗",
    "朝鲜",
    "古巴",
    "俄罗斯",
    "委内瑞拉",
    "白俄罗斯",
    "叙利亚",
    "Iran",
    "North Korea",
    "Cuba",
    "Russia",
    "Venezuela",
    "Belarus",
    "Syria",
]

_DEFAULT_WEIGHTS = {
    "writing": 0.5,
    "basic_info_completeness": 1.0,
    "id_validity": 1.5,
    "address_validity": 1.0,
    "logic_consistency": 1.5,
    "checkbox_consistency": 1.5,
    "beneficial_owner": 1.5,
    "attachment_presence": 1.0,
    "attachment_sanction_geo": 2.0,
    "attachment_vs_report": 1.5,
    "approval_compliance": 1.5,
}


class Settings:
    """运行时配置快照（构造时从环境变量读取一次）。"""
    """Plain settings object loaded from DD_CHECK_* env vars."""

    def __init__(self, **overrides: Any):
        self.host: str = overrides.get("host", _env("DD_CHECK_HOST", "127.0.0.1") or "127.0.0.1")
        self.port: int = overrides.get("port", _env_int("DD_CHECK_PORT", 8790))
        # MCP Streamable HTTP (separate process/port from REST by default)
        self.mcp_host: str = overrides.get(
            "mcp_host", _env("DD_CHECK_MCP_HOST", "127.0.0.1") or "127.0.0.1"
        )
        self.mcp_port: int = overrides.get("mcp_port", _env_int("DD_CHECK_MCP_PORT", 8791))
        self.strategy_dir: Optional[Path] = overrides.get("strategy_dir", _env_path("DD_CHECK_STRATEGY_DIR"))
        self.sqlite_path: Optional[Path] = overrides.get("sqlite_path", _env_path("DD_CHECK_SQLITE_PATH"))
        # LangGraph 持久 checkpoint（与结果表 sqlite_path 分开）
        self.checkpoint_sqlite_path: Optional[Path] = overrides.get(
            "checkpoint_sqlite_path",
            _env_path("DD_CHECK_CHECKPOINT_SQLITE_PATH"),
        )
        self.cust_type_map: Dict[str, str] = overrides.get(
            "cust_type_map",
            {k: str(v) for k, v in _env_json_dict("DD_CHECK_CUST_TYPE_MAP", _DEFAULT_CUST_MAP).items()},
        )
        self.high_risk_regions: List[str] = overrides.get(
            "high_risk_regions",
            _env_json_list("DD_CHECK_HIGH_RISK_REGIONS", _DEFAULT_REGIONS),
        )
        self.mysql_host: Optional[str] = overrides.get("mysql_host", _env("DD_CHECK_MYSQL_HOST"))
        self.mysql_port: int = overrides.get("mysql_port", _env_int("DD_CHECK_MYSQL_PORT", 3306))
        self.mysql_user: Optional[str] = overrides.get("mysql_user", _env("DD_CHECK_MYSQL_USER"))
        self.mysql_password: Optional[str] = overrides.get("mysql_password", _env("DD_CHECK_MYSQL_PASSWORD"))
        self.mysql_database: Optional[str] = overrides.get("mysql_database", _env("DD_CHECK_MYSQL_DATABASE"))
        self.mysql_ddp_file_table: str = overrides.get(
            "mysql_ddp_file_table", _env("DD_CHECK_MYSQL_DDP_FILE_TABLE", "ddp_file") or "ddp_file"
        )
        self.mysql_invest_id_column: str = overrides.get(
            "mysql_invest_id_column",
            _env("DD_CHECK_MYSQL_INVEST_ID_COLUMN", "invest_id") or "invest_id",
        )
        self.mysql_location_path_column: str = overrides.get(
            "mysql_location_path_column",
            _env("DD_CHECK_MYSQL_LOCATION_PATH_COLUMN", "location_path") or "location_path",
        )
        self.cos_secret_id: Optional[str] = overrides.get("cos_secret_id", _env("DD_CHECK_COS_SECRET_ID"))
        self.cos_secret_key: Optional[str] = overrides.get("cos_secret_key", _env("DD_CHECK_COS_SECRET_KEY"))
        self.cos_region: Optional[str] = overrides.get("cos_region", _env("DD_CHECK_COS_REGION"))
        self.cos_bucket: Optional[str] = overrides.get("cos_bucket", _env("DD_CHECK_COS_BUCKET"))
        self.cos_endpoint: Optional[str] = overrides.get("cos_endpoint", _env("DD_CHECK_COS_ENDPOINT"))
        self.cos_path_prefix: str = overrides.get(
            "cos_path_prefix", _env("DD_CHECK_COS_PATH_PREFIX", "") or ""
        )
        self.ecs_emode_b_key: Optional[str] = overrides.get(
            "ecs_emode_b_key", _env("DD_CHECK_ECS_EMODE_B_KEY")
        )
        self.attachment_max_bytes: int = overrides.get(
            "attachment_max_bytes", _env_int("DD_CHECK_ATTACHMENT_MAX_BYTES", 50 * 1024 * 1024)
        )
        self.attachment_max_files: int = overrides.get(
            "attachment_max_files", _env_int("DD_CHECK_ATTACHMENT_MAX_FILES", 20)
        )
        self.attachment_excerpt_max_chars: int = overrides.get(
            "attachment_excerpt_max_chars",
            _env_int("DD_CHECK_ATTACHMENT_EXCERPT_MAX_CHARS", 8000),
        )
        weights = _env_json_dict("DD_CHECK_SCORE_WEIGHTS", _DEFAULT_WEIGHTS)
        self.score_weights: Dict[str, float] = overrides.get(
            "score_weights",
            {str(k): float(v) for k, v in weights.items()},
        )
        self.llm_base_url: Optional[str] = overrides.get("llm_base_url", _env("DD_CHECK_LLM_BASE_URL"))
        self.llm_api_key: Optional[str] = overrides.get("llm_api_key", _env("DD_CHECK_LLM_API_KEY"))
        self.llm_model: Optional[str] = overrides.get("llm_model", _env("DD_CHECK_LLM_MODEL"))
        # Human-in-the-loop: pause after scoring for analyst confirm (MCP resume).
        self.hitl_enabled: bool = overrides.get(
            "hitl_enabled", _env_bool("DD_CHECK_HITL", False)
        )
        # When True with HITL: only interrupt if there is at least one FAIL finding.
        self.hitl_on_fail_only: bool = overrides.get(
            "hitl_on_fail_only", _env_bool("DD_CHECK_HITL_ON_FAIL_ONLY", False)
        )


def get_settings() -> Settings:
    """从环境变量构造 Settings（若存在 cwd/.env 则先灌入未设置的键）。"""
    # optional local .env without adding python-dotenv dependency
    env_file = Path.cwd() / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    return Settings()
