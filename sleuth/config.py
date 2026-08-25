"""Configuration loading — .env first, then optional JSONC overlays.

Primary knobs live in `.env` (see `.env.example`). Project `opencode.jsonc`
and markdown agents/commands remain supported as overlays for nested shapes
(MCP servers, custom agents).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def normalize_agent_key(name: str) -> str:
    """Collapse separators so ``ddreply``, ``dd_reply``, ``dd-reply`` match."""
    return "".join(c for c in (name or "").strip().lower() if c not in "-_")


@dataclass
class AgentConfig:
    name: str = "build"
    title: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    steps: int = 50
    permission: Dict[str, Any] = field(default_factory=dict)
    description: Optional[str] = None
    mode: str = "all"  # "primary" | "subagent" | "all"
    hidden: bool = False

    def merge(self, other: Dict[str, Any]) -> "AgentConfig":
        if "title" in other and other["title"] is not None:
            title = str(other["title"]).strip()
            if title:
                self.title = title
        if "prompt" in other and other["prompt"] is not None:
            self.prompt = other["prompt"]
        if "model" in other and other["model"] is not None:
            self.model = other["model"]
        if "steps" in other and other["steps"] is not None:
            self.steps = int(other["steps"])
        if "description" in other and other["description"] is not None:
            self.description = other["description"]
        if "permission" in other and isinstance(other["permission"], dict):
            self.permission.update(other["permission"])
        if "mode" in other and other["mode"] is not None:
            self.mode = str(other["mode"])
        if "hidden" in other and other["hidden"] is not None:
            self.hidden = bool(other["hidden"])
        return self


@dataclass
class CommandConfig:
    name: str
    template: str
    description: Optional[str] = None
    agent: Optional[str] = None


@dataclass
class McpServerConfig:
    name: str
    type: str = "remote"
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    command: Optional[List[str]] = None
    environment: Dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    timeout: Dict[str, int] = field(default_factory=dict)
    # Opt-in: after list_tools, call get_agent_card and register Agent (default False).
    agent: bool = False


@dataclass
class SkillS3Entry:
    uri: Optional[str] = None
    bucket: Optional[str] = None
    key: Optional[str] = None
    prefix: Optional[str] = None
    region: Optional[str] = None
    manifest: bool = False


@dataclass
class SkillsConfig:
    paths: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    s3: List[SkillS3Entry] = field(default_factory=list)
    refresh_seconds: int = 300


@dataclass
class StorageConfig:
    backend: str = "sqlite"  # sqlite | mysql
    sqlite_path: Optional[str] = None
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "sleuth"
    mysql_password: str = ""
    mysql_database: str = "sleuth"
    mysql_password_env: str = "SLEUTH_MYSQL_PASSWORD"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    admin_token: str = ""
    # Prefer mysql in production via SLEUTH_STORAGE_BACKEND=mysql
    default_backend: str = "sqlite"


@dataclass
class CosConfig:
    """Tencent COS / S3-compatible mailbox. All values come from env / JSONC."""

    secret_id: str = ""
    secret_key: str = ""
    region: str = ""
    bucket: str = ""
    endpoint: str = ""
    path_prefix: str = "sleuth/files"
    addressing_style: str = ""
    signature_version: str = ""

    def configured(self) -> bool:
        return bool(
            self.secret_id
            and self.secret_key
            and self.bucket
            and (self.region or self.endpoint)
        )


@dataclass
class FilesConfig:
    """Session file mailbox limits (upload / complete / generated return)."""

    max_bytes: int = 52_428_800
    max_count: int = 20
    presign_put_expires: int = 900
    presign_get_expires: int = 300
    # Empty or "*" = any MIME; otherwise CSV / JSONC list (supports "image/*").
    mime_allow: List[str] = field(default_factory=list)
    sm4_key: str = ""
    require_encrypt: bool = True
    excerpt_max_chars: int = 8000
    image_mode: str = "vision"
    vision_model: str = ""
    extract_concurrency: int = 2
    extract_timeout_s: float = 45.0
    prompt_wait_s: float = 8.0


@dataclass
class KbConfig:
    """Remote knowledge search for the default (build) agent — same API as dd_reply."""

    api_url: str = ""
    login_url: str = ""
    openid: str = ""
    service_id: str = ""
    api_timeout: float = 30.0
    sort_count: int = 10
    sort_score: Optional[float] = None
    time_combine: bool = False
    knowledge_ids: str = ""
    recall_count: int = 10
    atom_ids: str = ""
    node_ids: str = ""
    html_clear: bool = False
    qa_search_mode: str = ""
    time_filter_enable: bool = False
    time_filter_by_day: Optional[int] = None
    time_filter_start_time: str = ""
    time_filter_end_time: str = ""
    tag_name: str = ""
    tag_value_ids: str = ""
    tag_value_names: str = ""
    tag_search_operation: str = "AND"
    subnet_type: str = "dmz"

    def configured(self) -> bool:
        return bool(
            (self.api_url or "").strip()
            and (self.login_url or "").strip()
            and (self.openid or "").strip()
            and (self.service_id or "").strip()
        )


@dataclass
class MemoryConfig:
    """Long-term memory (OpenGauss). Defaults live here only — business code reads Config."""

    backend: str = "off"
    og_host: str = ""
    og_port: int = 5432
    og_user: str = ""
    og_password: str = ""
    og_database: str = ""
    og_dsn: str = ""
    og_schema: str = ""
    og_connect_timeout_s: float = 5.0
    table_item: str = "mem_item"
    table_audit: str = "mem_audit"
    embedding_model: str = ""
    embedding_dim: int = 1024
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    top_k: int = 12
    min_score: str = "0.35"
    max_items: int = 24
    max_chars: int = 6000
    pattern_ttl_days: int = 90
    ttl_kinds: str = "pattern"
    scenarios: str = "general,suspicious_analysis,due_diligence,screening,rating"
    kinds: str = "preference,workflow,policy,fact,pattern,forget"
    pin_kinds: str = "preference,forget"
    vector_kind: str = "vector"
    scope_kinds: str = "user,role,org"
    origins: str = "user_explicit,agent_inferred,admin"
    row_status_active: str = "active"
    row_status_archived: str = "archived"


@dataclass
class AclConfig:
    """Identity directory + agent/skill grants on the session MySQL/SQLite database."""

    enabled: bool = False
    table_org: str = "mem_org"
    table_role: str = "mem_role"
    table_user: str = "mem_user"
    table_grant: str = "mem_grant"
    default_agent_open: bool = True
    default_agent_name: str = ""
    row_status_active: str = "active"
    row_status_disabled: str = "disabled"
    grant_allow: str = "allow"
    grant_deny: str = "deny"
    scope_kinds: str = "role,org,user"
    resource_kinds: str = "agent,skill"


@dataclass
class Config:
    model: Optional[str] = None
    small_model: Optional[str] = None
    # Named model catalog for mid-session switching.
    # Values are either a ref string ("provider/model") or an object
    # {"model": "deepseek-chat", "apiKey": "...", "baseURL": "..."} — no
    # provider prefix required; the alias name is used as the provider id.
    models: Dict[str, Any] = field(default_factory=dict)
    default_agent: str = "build"
    user_id: str = "local"
    agents: Dict[str, AgentConfig] = field(default_factory=dict)
    # Extra lookup keys → canonical agent name (MCP server id, punctuation variants).
    agent_aliases: Dict[str, str] = field(default_factory=dict)
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    permission: Dict[str, Any] = field(default_factory=dict)
    instructions: List[str] = field(default_factory=list)
    tool_output_max_lines: int = 2000
    tool_output_max_bytes: int = 50_000
    max_steps: int = 50
    subagent_depth: int = 1
    context_limit: int = 128_000
    compaction: Dict[str, Any] = field(default_factory=lambda: {"auto": True})
    commands: Dict[str, CommandConfig] = field(default_factory=dict)
    mcp_servers: Dict[str, McpServerConfig] = field(default_factory=dict)
    mcp_timeout: Dict[str, int] = field(
        default_factory=lambda: {"startup": 30_000, "request": 120_000}
    )
    # Background retry for servers that were down at startup. 0 = off (manual /mcp reload only).
    mcp_retry_seconds: int = 15
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    cos: CosConfig = field(default_factory=CosConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    kb: KbConfig = field(default_factory=KbConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    acl: AclConfig = field(default_factory=AclConfig)
    # Product disclosure guardrails (block sleuth internals / secrets).
    guardrails: bool = True
    # Scrub PII (ID / mobile / bank / password / labeled address) on outputs.
    output_desensitize: bool = True

    def resolve_agent_name(self, name: Optional[str] = None) -> str:
        """Map a request/CLI name to the registered agent id.

        Accepts MCP server keys (``ddreply`` → ``dd_reply``) and punctuation
        variants. Unknown names are returned unchanged.
        """
        raw = (name or self.default_agent or "build").strip()
        if not raw:
            return (self.default_agent or "build").strip() or "build"
        if raw in self.agents:
            return raw
        aliases = self.agent_aliases or {}
        nkey = normalize_agent_key(raw)
        for candidate in (raw, nkey):
            if candidate and candidate in aliases:
                canon = aliases[candidate]
                if canon in self.agents:
                    return canon
        for key in self.agents:
            if normalize_agent_key(key) == nkey:
                return key
        if raw in aliases:
            return aliases[raw]
        return raw

    def agent(self, name: Optional[str] = None) -> AgentConfig:
        resolved = self.resolve_agent_name(name)
        found = self.agents.get(resolved)
        if found is not None:
            return found
        return AgentConfig(name=resolved)

    def register_agent_alias(self, alias: str, canonical: str) -> None:
        alias = (alias or "").strip()
        canonical = (canonical or "").strip()
        if not alias or not canonical:
            return
        self.agent_aliases[alias] = canonical
        nkey = normalize_agent_key(alias)
        if nkey:
            self.agent_aliases[nkey] = canonical

    def provider_options(self, provider_id: str) -> Dict[str, Any]:
        return self.providers.get(provider_id, {}).get("options", {})

    def resolve_model_alias(self, name: str) -> str:
        """Expand a catalog entry into ``provider/model`` (seeds credentials)."""
        return self.prepare_model_ref(name)

    def prepare_model_ref(self, name: str) -> str:
        """Resolve alias/ref and attach per-model credentials when present.

        Catalog entry shapes::

            "ds": "deepseek/deepseek-chat"
            "deepseek-chat": {"apiKey": "sk-...", "baseURL": "https://..."}
            "ds": {"model": "deepseek-chat", "apiKey": "...", "baseURL": "..."}

        Object entries do not need a provider prefix: the alias becomes the
        internal provider id used to look up apiKey/baseURL.
        """
        key = (name or "").strip()
        if not key:
            return key
        entry = self.models.get(key)
        if entry is None:
            return key
        if isinstance(entry, str):
            return entry.strip() or key
        if not isinstance(entry, dict):
            return key
        model_id = str(entry.get("model") or entry.get("id") or key).strip()
        provider_id = str(entry.get("provider") or key).strip() or key
        opts = self.providers.setdefault(provider_id, {}).setdefault("options", {})
        api_key = entry.get("apiKey") or entry.get("api_key")
        base_url = entry.get("baseURL") or entry.get("base_url")
        if api_key:
            opts["apiKey"] = str(api_key)
        if base_url:
            opts["baseURL"] = str(base_url)
        return f"{provider_id}/{model_id}"

    def model_entry_label(self, alias: str) -> str:
        """Human-readable label for ``/model`` listing."""
        entry = self.models.get(alias)
        if entry is None:
            return alias
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            model_id = entry.get("model") or entry.get("id") or alias
            base = entry.get("baseURL") or entry.get("base_url") or ""
            if base:
                return f"{model_id} @ {base}"
            return str(model_id)
        return str(entry)

    def enabled_mcp_servers(self) -> List[McpServerConfig]:
        return [s for s in self.mcp_servers.values() if not s.disabled]

    def merge(self, raw: Dict[str, Any]) -> "Config":
        if "model" in raw and raw["model"]:
            self.model = raw["model"]
        if "small_model" in raw and raw["small_model"]:
            self.small_model = raw["small_model"]
        if "models" in raw and isinstance(raw["models"], dict):
            for alias, entry in raw["models"].items():
                if not alias or entry in (None, ""):
                    continue
                if isinstance(entry, (str, dict)):
                    self.models[str(alias)] = entry
        if "default_agent" in raw and raw["default_agent"]:
            self.default_agent = raw["default_agent"]
        if "user_id" in raw and raw["user_id"]:
            self.user_id = str(raw["user_id"])
        if "max_steps" in raw and raw["max_steps"] is not None:
            self.max_steps = int(raw["max_steps"])
        if "subagent_depth" in raw and raw["subagent_depth"] is not None:
            self.subagent_depth = int(raw["subagent_depth"])
        if "context_limit" in raw and raw["context_limit"] is not None:
            self.context_limit = int(raw["context_limit"])
        if "compaction" in raw and isinstance(raw["compaction"], dict):
            self.compaction.update(raw["compaction"])
        if "instructions" in raw and isinstance(raw["instructions"], list):
            self.instructions.extend(raw["instructions"])
        if "permission" in raw and isinstance(raw["permission"], dict):
            self.permission.update(raw["permission"])
        if "provider" in raw and isinstance(raw["provider"], dict):
            for pid, pconf in raw["provider"].items():
                self.providers.setdefault(pid, {}).update(pconf)
        if "tool_output" in raw and isinstance(raw["tool_output"], dict):
            if "max_lines" in raw["tool_output"]:
                self.tool_output_max_lines = int(raw["tool_output"]["max_lines"])
            if "max_bytes" in raw["tool_output"]:
                self.tool_output_max_bytes = int(raw["tool_output"]["max_bytes"])
        if "agent" in raw and isinstance(raw["agent"], dict):
            for name, acfg in raw["agent"].items():
                if not isinstance(acfg, dict):
                    continue
                existing = self.agents.setdefault(name, AgentConfig(name=name))
                existing.merge(acfg)
        if "command" in raw and isinstance(raw["command"], dict):
            for name, ccfg in raw["command"].items():
                if not isinstance(ccfg, dict):
                    continue
                template = ccfg.get("template") or ""
                self.commands[name] = CommandConfig(
                    name=name,
                    template=str(template),
                    description=ccfg.get("description"),
                    agent=ccfg.get("agent"),
                )
        if "storage" in raw and isinstance(raw["storage"], dict):
            self._merge_storage(raw["storage"])
        if "server" in raw and isinstance(raw["server"], dict):
            self._merge_server(raw["server"])
        if "cos" in raw and isinstance(raw["cos"], dict):
            self._merge_cos(raw["cos"])
        if "files" in raw and isinstance(raw["files"], dict):
            self._merge_files(raw["files"])
        if "kb" in raw and isinstance(raw["kb"], dict):
            self._merge_kb(raw["kb"])
        if "memory" in raw and isinstance(raw["memory"], dict):
            self._merge_memory(raw["memory"])
        if "acl" in raw and isinstance(raw["acl"], dict):
            self._merge_acl(raw["acl"])
        self._merge_mcp(raw)
        self._merge_skills(raw)
        return self

    def _merge_storage(self, block: Dict[str, Any]) -> None:
        if block.get("backend"):
            self.storage.backend = str(block["backend"])
        sqlite = block.get("sqlite") or {}
        if isinstance(sqlite, dict) and sqlite.get("path") is not None:
            self.storage.sqlite_path = str(sqlite["path"]) if sqlite["path"] else None
        if block.get("sqlite_path") is not None:
            self.storage.sqlite_path = str(block["sqlite_path"]) if block["sqlite_path"] else None
        mysql = block.get("mysql") or {}
        if isinstance(mysql, dict):
            if mysql.get("host"):
                self.storage.mysql_host = str(mysql["host"])
            if mysql.get("port") is not None:
                self.storage.mysql_port = int(mysql["port"])
            if mysql.get("user"):
                self.storage.mysql_user = str(mysql["user"])
            if mysql.get("password") is not None:
                self.storage.mysql_password = str(mysql["password"])
            if mysql.get("database"):
                self.storage.mysql_database = str(mysql["database"])
            if mysql.get("password_env"):
                self.storage.mysql_password_env = str(mysql["password_env"])

    def _merge_server(self, block: Dict[str, Any]) -> None:
        if block.get("host"):
            self.server.host = str(block["host"])
        if block.get("port") is not None:
            self.server.port = int(block["port"])
        if block.get("admin_token") is not None:
            self.server.admin_token = str(block["admin_token"])
        if block.get("default_backend"):
            self.server.default_backend = str(block["default_backend"])

    def _merge_cos(self, block: Dict[str, Any]) -> None:
        """Mailbox-only knobs. Credentials / bucket / endpoint come from AWS_* + Skills S3."""
        for src, attr in (
            ("path_prefix", "path_prefix"),
            ("pathPrefix", "path_prefix"),
            ("addressing_style", "addressing_style"),
            ("addressingStyle", "addressing_style"),
            ("signature_version", "signature_version"),
            ("signatureVersion", "signature_version"),
        ):
            val = block.get(src)
            if val is None or val == "":
                continue
            setattr(self.cos, attr, str(val))

    def _merge_files(self, block: Dict[str, Any]) -> None:
        for src, attr in (
            ("max_bytes", "max_bytes"),
            ("maxBytes", "max_bytes"),
            ("max_count", "max_count"),
            ("maxCount", "max_count"),
            ("presign_put_expires", "presign_put_expires"),
            ("presignPutExpires", "presign_put_expires"),
            ("presign_get_expires", "presign_get_expires"),
            ("presignGetExpires", "presign_get_expires"),
            ("excerpt_max_chars", "excerpt_max_chars"),
            ("excerptMaxChars", "excerpt_max_chars"),
            ("extract_concurrency", "extract_concurrency"),
            ("extractConcurrency", "extract_concurrency"),
        ):
            val = block.get(src)
            if val is None or val == "":
                continue
            try:
                setattr(self.files, attr, int(val))
            except (TypeError, ValueError):
                continue
        for src, attr in (
            ("extract_timeout_s", "extract_timeout_s"),
            ("extractTimeoutS", "extract_timeout_s"),
            ("prompt_wait_s", "prompt_wait_s"),
            ("promptWaitS", "prompt_wait_s"),
        ):
            val = block.get(src)
            if val is None or val == "":
                continue
            try:
                setattr(self.files, attr, float(val))
            except (TypeError, ValueError):
                continue
        for src, attr in (
            ("sm4_key", "sm4_key"),
            ("sm4Key", "sm4_key"),
            ("image_mode", "image_mode"),
            ("imageMode", "image_mode"),
            ("vision_model", "vision_model"),
            ("visionModel", "vision_model"),
        ):
            val = block.get(src)
            if val is None:
                continue
            setattr(self.files, attr, str(val))
        req = block.get("require_encrypt")
        if req is None:
            req = block.get("requireEncrypt")
        if req is not None:
            if isinstance(req, bool):
                self.files.require_encrypt = req
            else:
                self.files.require_encrypt = str(req).strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
        mime = block.get("mime_allow")
        if mime is None:
            mime = block.get("mimeAllow")
        parsed = _parse_mime_allow(mime)
        if parsed is not None:
            self.files.mime_allow = parsed

    def _merge_kb(self, block: Dict[str, Any]) -> None:
        def _pick(*keys: str) -> Any:
            for key in keys:
                if key in block and block[key] not in (None, ""):
                    return block[key]
            return None

        for srcs, attr in (
            (("api_url", "apiUrl"), "api_url"),
            (("login_url", "loginUrl"), "login_url"),
            (("openid", "openId"), "openid"),
            (("service_id", "serviceId"), "service_id"),
            (("knowledge_ids", "knowledgeIds"), "knowledge_ids"),
            (("atom_ids", "atomIds"), "atom_ids"),
            (("node_ids", "nodeIds"), "node_ids"),
            (("qa_search_mode", "qaSearchMode"), "qa_search_mode"),
            (("time_filter_start_time", "timeFilterStartTime"), "time_filter_start_time"),
            (("time_filter_end_time", "timeFilterEndTime"), "time_filter_end_time"),
            (("tag_name", "tagName"), "tag_name"),
            (("tag_value_ids", "tagValueIds"), "tag_value_ids"),
            (("tag_value_names", "tagValueNames"), "tag_value_names"),
            (("tag_search_operation", "tagSearchOperation"), "tag_search_operation"),
            (("subnet_type", "subnetType"), "subnet_type"),
        ):
            val = _pick(*srcs)
            if val is not None:
                setattr(self.kb, attr, str(val))
        # Single knowledgeId is accepted as a convenience alias.
        if not self.kb.knowledge_ids:
            single = _pick("knowledge_id", "knowledgeId")
            if single is not None:
                self.kb.knowledge_ids = str(single)

        timeout = _pick("api_timeout", "apiTimeout")
        if timeout is not None:
            try:
                self.kb.api_timeout = float(timeout)
            except (TypeError, ValueError):
                pass
        for srcs, attr in (
            (("sort_count", "sortCount"), "sort_count"),
            (("recall_count", "recallCount"), "recall_count"),
            (("time_filter_by_day", "timeFilterByDay"), "time_filter_by_day"),
        ):
            val = _pick(*srcs)
            if val is None:
                continue
            try:
                setattr(self.kb, attr, int(val))
            except (TypeError, ValueError):
                continue
        score = _pick("sort_score", "sortScore")
        if score is not None:
            try:
                self.kb.sort_score = float(score)
            except (TypeError, ValueError):
                pass
        for srcs, attr in (
            (("time_combine", "timeCombine"), "time_combine"),
            (("html_clear", "htmlClear"), "html_clear"),
            (("time_filter_enable", "timeFilterEnable"), "time_filter_enable"),
        ):
            val = _pick(*srcs)
            if val is None:
                continue
            if isinstance(val, bool):
                setattr(self.kb, attr, val)
            else:
                setattr(self.kb, attr, str(val).strip().lower() in ("1", "true", "yes", "on"))

    def _merge_memory(self, block: Dict[str, Any]) -> None:
        str_keys = (
            ("backend", "backend"),
            ("og_host", "og_host"),
            ("ogHost", "og_host"),
            ("og_user", "og_user"),
            ("ogUser", "og_user"),
            ("og_password", "og_password"),
            ("ogPassword", "og_password"),
            ("og_database", "og_database"),
            ("ogDatabase", "og_database"),
            ("og_dsn", "og_dsn"),
            ("ogDsn", "og_dsn"),
            ("og_schema", "og_schema"),
            ("ogSchema", "og_schema"),
            ("table_item", "table_item"),
            ("tableItem", "table_item"),
            ("table_audit", "table_audit"),
            ("tableAudit", "table_audit"),
            ("embedding_model", "embedding_model"),
            ("embeddingModel", "embedding_model"),
            ("embedding_base_url", "embedding_base_url"),
            ("embeddingBaseUrl", "embedding_base_url"),
            ("embedding_api_key", "embedding_api_key"),
            ("embeddingApiKey", "embedding_api_key"),
            ("min_score", "min_score"),
            ("minScore", "min_score"),
            ("ttl_kinds", "ttl_kinds"),
            ("ttlKinds", "ttl_kinds"),
            ("scenarios", "scenarios"),
            ("kinds", "kinds"),
            ("pin_kinds", "pin_kinds"),
            ("pinKinds", "pin_kinds"),
            ("vector_kind", "vector_kind"),
            ("vectorKind", "vector_kind"),
            ("scope_kinds", "scope_kinds"),
            ("scopeKinds", "scope_kinds"),
            ("origins", "origins"),
            ("row_status_active", "row_status_active"),
            ("rowStatusActive", "row_status_active"),
            ("row_status_archived", "row_status_archived"),
            ("rowStatusArchived", "row_status_archived"),
        )
        for src, attr in str_keys:
            val = block.get(src)
            if val is None:
                continue
            setattr(self.memory, attr, str(val))
        int_keys = (
            ("og_port", "og_port"),
            ("ogPort", "og_port"),
            ("embedding_dim", "embedding_dim"),
            ("embeddingDim", "embedding_dim"),
            ("top_k", "top_k"),
            ("topK", "top_k"),
            ("max_items", "max_items"),
            ("maxItems", "max_items"),
            ("max_chars", "max_chars"),
            ("maxChars", "max_chars"),
            ("pattern_ttl_days", "pattern_ttl_days"),
            ("patternTtlDays", "pattern_ttl_days"),
        )
        for src, attr in int_keys:
            val = block.get(src)
            if val is None or val == "":
                continue
            try:
                setattr(self.memory, attr, int(val))
            except (TypeError, ValueError):
                continue
        timeout = block.get("og_connect_timeout_s") or block.get("ogConnectTimeoutS")
        if timeout is not None and timeout != "":
            try:
                self.memory.og_connect_timeout_s = float(timeout)
            except (TypeError, ValueError):
                pass

    def _merge_acl(self, block: Dict[str, Any]) -> None:
        str_keys = (
            ("table_org", "table_org"),
            ("tableOrg", "table_org"),
            ("table_role", "table_role"),
            ("tableRole", "table_role"),
            ("table_user", "table_user"),
            ("tableUser", "table_user"),
            ("table_grant", "table_grant"),
            ("tableGrant", "table_grant"),
            ("default_agent_name", "default_agent_name"),
            ("defaultAgentName", "default_agent_name"),
            ("row_status_active", "row_status_active"),
            ("rowStatusActive", "row_status_active"),
            ("row_status_disabled", "row_status_disabled"),
            ("rowStatusDisabled", "row_status_disabled"),
            ("grant_allow", "grant_allow"),
            ("grantAllow", "grant_allow"),
            ("grant_deny", "grant_deny"),
            ("grantDeny", "grant_deny"),
            ("scope_kinds", "scope_kinds"),
            ("scopeKinds", "scope_kinds"),
            ("resource_kinds", "resource_kinds"),
            ("resourceKinds", "resource_kinds"),
        )
        for src, attr in str_keys:
            val = block.get(src)
            if val is None:
                continue
            setattr(self.acl, attr, str(val))
        for src, attr in (
            ("enabled", "enabled"),
            ("default_agent_open", "default_agent_open"),
            ("defaultAgentOpen", "default_agent_open"),
        ):
            val = block.get(src)
            if val is None:
                continue
            if isinstance(val, bool):
                setattr(self.acl, attr, val)
            else:
                setattr(self.acl, attr, str(val).strip().lower() in ("1", "true", "yes", "on"))

    def _merge_mcp(self, raw: Dict[str, Any]) -> None:
        if "mcpServers" in raw and isinstance(raw["mcpServers"], dict):
            for name, entry in raw["mcpServers"].items():
                if isinstance(entry, dict):
                    self.mcp_servers[name] = _parse_mcp_server(name, entry)

        block = raw.get("mcp")
        if not isinstance(block, dict):
            return
        timeout = block.get("timeout")
        if isinstance(timeout, dict):
            self.mcp_timeout.update(
                {k: int(v) for k, v in timeout.items() if v is not None}
            )
        servers = block.get("servers")
        if isinstance(servers, dict):
            for name, entry in servers.items():
                if isinstance(entry, dict):
                    self.mcp_servers[name] = _parse_mcp_server(name, entry)
        if block.get("retry_seconds") is not None:
            self.mcp_retry_seconds = int(block["retry_seconds"])

    def _merge_skills(self, raw: Dict[str, Any]) -> None:
        block = raw.get("skills")
        if not isinstance(block, dict):
            return
        if block.get("refresh_seconds") is not None:
            self.skills.refresh_seconds = int(block["refresh_seconds"])
        paths = block.get("paths")
        if isinstance(paths, list):
            for p in paths:
                if isinstance(p, str) and p not in self.skills.paths:
                    self.skills.paths.append(p)
        urls = block.get("urls")
        if isinstance(urls, list):
            for u in urls:
                if isinstance(u, str) and u not in self.skills.urls:
                    self.skills.urls.append(u)
        s3 = block.get("s3")
        if isinstance(s3, list):
            for item in s3:
                entry = _parse_s3_entry(item)
                if entry is not None:
                    self.skills.s3.append(entry)


def _parse_s3_entry(item: Any) -> Optional[SkillS3Entry]:
    if isinstance(item, str):
        return SkillS3Entry(uri=item)
    if not isinstance(item, dict):
        return None
    return SkillS3Entry(
        uri=str(item["uri"]) if item.get("uri") else None,
        bucket=str(item["bucket"]) if item.get("bucket") else None,
        key=str(item["key"]) if item.get("key") else None,
        prefix=str(item["prefix"]) if item.get("prefix") else None,
        region=str(item["region"]) if item.get("region") else None,
        manifest=bool(item.get("manifest", False)),
    )


def _parse_mcp_server(name: str, entry: Dict[str, Any]) -> McpServerConfig:
    stype = entry.get("type")
    url = entry.get("url")
    command = entry.get("command")
    if stype is None:
        if url:
            stype = "remote"
        elif command:
            stype = "local"
        else:
            stype = "remote"

    cmd_list: Optional[List[str]] = None
    if isinstance(command, list):
        cmd_list = [str(c) for c in command]
    elif isinstance(command, str):
        args = entry.get("args") or []
        cmd_list = [command] + [str(a) for a in args]

    headers = entry.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    env = entry.get("environment") or entry.get("env") or {}
    if not isinstance(env, dict):
        env = {}
    timeout = entry.get("timeout") or {}
    if not isinstance(timeout, dict):
        timeout = {}

    agent_flag = entry.get("agent", False)
    if isinstance(agent_flag, str):
        agent_flag = agent_flag.strip().lower() in {"1", "true", "yes", "on"}

    return McpServerConfig(
        name=name,
        type=str(stype),
        url=str(url) if url else None,
        headers={str(k): str(v) for k, v in headers.items()},
        command=cmd_list,
        environment={str(k): str(v) for k, v in env.items()},
        disabled=bool(entry.get("disabled", False)),
        timeout={str(k): int(v) for k, v in timeout.items()},
        agent=bool(agent_flag),
    )


def _strip_comments(text: str) -> str:
    out: List[str] = []
    i = 0
    n = len(text)
    in_string = False
    string_char = ""
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == string_char:
                in_string = False
            i += 1
            continue
        if c in ('"', "'"):
            in_string = True
            string_char = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _load_jsonc(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    stripped = _strip_comments(raw)
    if not stripped.strip():
        return {}
    return json.loads(stripped)


def _global_config_dir() -> Path:
    env = os.environ.get("OPENCODE_CONFIG_DIR") or os.environ.get("XDG_CONFIG_HOME")
    if env:
        return Path(env) / "opencode"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "opencode"
    return Path.home() / ".config" / "opencode"


_CONFIG_FILENAMES = ("opencode.jsonc", "opencode.json", "config.json")


def _walk_project_configs(cwd: Path) -> List[Path]:
    found: List[Path] = []
    root = _git_root(cwd) or _fs_root(cwd)
    cur = cwd.resolve()
    while True:
        for name in _CONFIG_FILENAMES:
            p = cur / name
            if p.is_file():
                found.append(p)
        for name in _CONFIG_FILENAMES:
            p = cur / ".opencode" / name
            if p.is_file():
                found.append(p)
        if cur == root or cur == cur.parent:
            break
        cur = cur.parent
    return found


def _git_root(cwd: Path) -> Optional[Path]:
    cur = cwd.resolve()
    while True:
        if (cur / ".git").is_dir():
            return cur
        if cur == cur.parent:
            return None
        cur = cur.parent


def _fs_root(cwd: Path) -> Path:
    anchor = cwd.resolve().anchor
    return Path(anchor) if anchor else Path("/")


def _load_markdown_agents(dir_path: Path, cfg: Config) -> None:
    from .util.markdown_fm import entry_name_from_path, parse_file

    if not dir_path.is_dir():
        return
    for pattern_root in ("agent", "agents"):
        root = dir_path / pattern_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                md = parse_file(path)
            except OSError:
                continue
            rel = str(path.relative_to(dir_path))
            name = entry_name_from_path(rel, ("agent/", "agents/"))
            data = dict(md.data)
            data["prompt"] = md.content
            existing = cfg.agents.setdefault(name, AgentConfig(name=name))
            existing.merge(data)


def _load_markdown_commands(dir_path: Path, cfg: Config) -> None:
    from .util.markdown_fm import entry_name_from_path, parse_file

    if not dir_path.is_dir():
        return
    for pattern_root in ("command", "commands"):
        root = dir_path / pattern_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                md = parse_file(path)
            except OSError:
                continue
            rel = str(path.relative_to(dir_path))
            name = entry_name_from_path(rel, ("command/", "commands/"))
            cfg.commands[name] = CommandConfig(
                name=name,
                template=md.content,
                description=md.data.get("description"),
                agent=md.data.get("agent"),
            )


def _opencode_dirs(cwd: Path) -> List[Path]:
    dirs: List[Path] = []
    gdir = _global_config_dir()
    if gdir.is_dir():
        dirs.append(gdir)
    root = _git_root(cwd) or _fs_root(cwd)
    cur = cwd.resolve()
    found: List[Path] = []
    while True:
        p = cur / ".opencode"
        if p.is_dir():
            found.append(p)
        if cur == root or cur == cur.parent:
            break
        cur = cur.parent
    dirs.extend(reversed(found))
    return dirs


def _env_bool(name: str, default: Optional[bool] = None) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_csv(name: str) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_mime_allow(raw: Any) -> Optional[List[str]]:
    """None = leave unchanged; empty / * = allow all (empty list)."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip() and str(x).strip() != "*"]
    text = str(raw).strip()
    if not text or text == "*":
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip() and str(x).strip() != "*"]
    return [p.strip() for p in text.split(",") if p.strip() and p.strip() != "*"]


def _parse_models_env(raw: str) -> Dict[str, Any]:
    """Parse SLEUTH_MODELS as JSON object or ``alias:provider/model`` CSV.

    JSON values may be strings or credential objects::

        {"deepseek-chat": {"apiKey": "sk-...", "baseURL": "https://..."}}
        {"ds": "deepseek/deepseek-chat"}
    """
    text = (raw or "").strip()
    if not text:
        return {}
    out: Dict[str, Any] = {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            for alias, entry in data.items():
                if not alias or entry in (None, ""):
                    continue
                if isinstance(entry, (str, dict)):
                    out[str(alias)] = entry
        return out
    for part in text.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        alias, _, ref = part.partition(":")
        alias, ref = alias.strip(), ref.strip()
        if alias and ref:
            out[alias] = ref
    return out


def _bucket_from_skills_s3(cfg: Config) -> str:
    """Bucket already declared on Skills S3 (uri or bucket field)."""
    for entry in cfg.skills.s3 or []:
        if entry.bucket:
            return str(entry.bucket).strip()
        uri = (entry.uri or "").strip()
        if uri.startswith("s3://"):
            bucket = uri[5:].split("/", 1)[0].strip()
            if bucket:
                return bucket
    return ""


def _apply_shared_object_store(cfg: Config) -> None:
    """Session files use the same COS as Skills: AWS_* + SLEUTH_S3_ENDPOINT + Skills bucket."""
    cfg.cos.secret_id = os.environ.get("AWS_ACCESS_KEY_ID") or ""
    cfg.cos.secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
    cfg.cos.region = os.environ.get("AWS_DEFAULT_REGION") or ""
    cfg.cos.endpoint = (
        os.environ.get("SLEUTH_S3_ENDPOINT")
        or os.environ.get("AWS_ENDPOINT_URL")
        or ""
    )
    cfg.cos.bucket = _bucket_from_skills_s3(cfg)


def _apply_env(cfg: Config) -> None:
    """Map SLEUTH_* / OPENCODE_* environment variables onto Config."""
    model = os.environ.get("SLEUTH_MODEL") or os.environ.get("OPENCODE_MODEL")
    if model:
        cfg.model = model
    small = os.environ.get("SLEUTH_SMALL_MODEL") or os.environ.get("OPENCODE_SMALL_MODEL")
    if small:
        cfg.small_model = small
    models_raw = os.environ.get("SLEUTH_MODELS")
    if models_raw:
        cfg.models.update(_parse_models_env(models_raw))
    agent = os.environ.get("SLEUTH_DEFAULT_AGENT") or os.environ.get("OPENCODE_DEFAULT_AGENT")
    if agent:
        cfg.default_agent = agent
    user = os.environ.get("SLEUTH_USER_ID") or os.environ.get("OPENCODE_USER_ID")
    if user:
        cfg.user_id = user

    guard = _env_bool("SLEUTH_GUARDRAILS")
    if guard is not None:
        cfg.guardrails = guard

    desense = _env_bool("SLEUTH_OUTPUT_DESENSITIZE")
    if desense is not None:
        cfg.output_desensitize = desense

    for key, attr in (
        ("SLEUTH_MAX_STEPS", "max_steps"),
        ("OPENCODE_MAX_STEPS", "max_steps"),
        ("SLEUTH_SUBAGENT_DEPTH", "subagent_depth"),
        ("SLEUTH_CONTEXT_LIMIT", "context_limit"),
        ("OPENCODE_CONTEXT_LIMIT", "context_limit"),
        ("SLEUTH_TOOL_OUTPUT_MAX_LINES", "tool_output_max_lines"),
        ("SLEUTH_TOOL_OUTPUT_MAX_BYTES", "tool_output_max_bytes"),
    ):
        val = _env_int(key)
        if val is not None:
            setattr(cfg, attr, val)

    auto = _env_bool("SLEUTH_COMPACTION_AUTO")
    if auto is not None:
        cfg.compaction["auto"] = auto
    reserved = _env_int("SLEUTH_COMPACTION_RESERVED")
    if reserved is not None:
        cfg.compaction["reserved"] = reserved

    # storage
    backend = os.environ.get("SLEUTH_STORAGE_BACKEND")
    if backend:
        cfg.storage.backend = backend.strip().lower()
    if os.environ.get("SLEUTH_SQLITE_PATH"):
        cfg.storage.sqlite_path = os.environ["SLEUTH_SQLITE_PATH"]
    if os.environ.get("SLEUTH_MYSQL_HOST"):
        cfg.storage.mysql_host = os.environ["SLEUTH_MYSQL_HOST"]
    port = _env_int("SLEUTH_MYSQL_PORT")
    if port is not None:
        cfg.storage.mysql_port = port
    if os.environ.get("SLEUTH_MYSQL_USER"):
        cfg.storage.mysql_user = os.environ["SLEUTH_MYSQL_USER"]
    if os.environ.get("SLEUTH_MYSQL_PASSWORD"):
        cfg.storage.mysql_password = os.environ["SLEUTH_MYSQL_PASSWORD"]
    if os.environ.get("SLEUTH_MYSQL_DATABASE"):
        cfg.storage.mysql_database = os.environ["SLEUTH_MYSQL_DATABASE"]
    if os.environ.get("SLEUTH_MYSQL_PASSWORD_ENV"):
        cfg.storage.mysql_password_env = os.environ["SLEUTH_MYSQL_PASSWORD_ENV"]

    # server
    if os.environ.get("SLEUTH_SERVER_HOST"):
        cfg.server.host = os.environ["SLEUTH_SERVER_HOST"]
    sport = _env_int("SLEUTH_SERVER_PORT")
    if sport is not None:
        cfg.server.port = sport
    if os.environ.get("SLEUTH_SERVER_ADMIN_TOKEN") is not None:
        cfg.server.admin_token = os.environ.get("SLEUTH_SERVER_ADMIN_TOKEN") or ""
    if os.environ.get("SLEUTH_SERVER_DEFAULT_BACKEND"):
        cfg.server.default_backend = os.environ["SLEUTH_SERVER_DEFAULT_BACKEND"]

    # session files (mailbox-only knobs; COS identity is the shared AWS / Skills S3 set)
    for env_key, attr in (
        ("SLEUTH_COS_PATH_PREFIX", "path_prefix"),
        ("SLEUTH_COS_ADDRESSING_STYLE", "addressing_style"),
        ("SLEUTH_COS_SIGNATURE_VERSION", "signature_version"),
    ):
        val = os.environ.get(env_key)
        if val:
            setattr(cfg.cos, attr, val)

    for env_key, attr in (
        ("SLEUTH_FILES_MAX_BYTES", "max_bytes"),
        ("SLEUTH_FILES_MAX_COUNT", "max_count"),
        ("SLEUTH_FILES_PRESIGN_PUT_EXPIRES", "presign_put_expires"),
        ("SLEUTH_FILES_PRESIGN_GET_EXPIRES", "presign_get_expires"),
        ("SLEUTH_FILES_EXCERPT_MAX_CHARS", "excerpt_max_chars"),
        ("SLEUTH_FILES_EXTRACT_CONCURRENCY", "extract_concurrency"),
    ):
        val = _env_int(env_key)
        if val is not None:
            setattr(cfg.files, attr, val)
    timeout = _env_float("SLEUTH_FILES_EXTRACT_TIMEOUT_S")
    if timeout is not None:
        cfg.files.extract_timeout_s = timeout
    wait_s = _env_float("SLEUTH_FILES_PROMPT_WAIT_S")
    if wait_s is not None:
        cfg.files.prompt_wait_s = wait_s
    if os.environ.get("SLEUTH_SM4_KEY") is not None:
        cfg.files.sm4_key = os.environ.get("SLEUTH_SM4_KEY") or ""
    if os.environ.get("SLEUTH_FILES_IMAGE_MODE"):
        cfg.files.image_mode = os.environ["SLEUTH_FILES_IMAGE_MODE"].strip() or "vision"
    if os.environ.get("SLEUTH_FILES_VISION_MODEL") is not None:
        cfg.files.vision_model = os.environ.get("SLEUTH_FILES_VISION_MODEL") or ""
    req_enc = _env_bool("SLEUTH_FILES_REQUIRE_ENCRYPT")
    if req_enc is not None:
        cfg.files.require_encrypt = req_enc
    mime_parsed = _parse_mime_allow(os.environ.get("SLEUTH_FILES_MIME_ALLOW"))
    if mime_parsed is not None and os.environ.get("SLEUTH_FILES_MIME_ALLOW") is not None:
        cfg.files.mime_allow = mime_parsed

    # remote KB (default agent) — login Cookie ragToken + optional serviceConfig
    for env_key, attr in (
        ("SLEUTH_KB_API_URL", "api_url"),
        ("SLEUTH_KB_LOGIN_URL", "login_url"),
        ("SLEUTH_KB_OPENID", "openid"),
        ("SLEUTH_KB_SERVICEID", "service_id"),
        ("SLEUTH_KB_KNOWLEDGE_IDS", "knowledge_ids"),
        ("SLEUTH_KB_ATOM_IDS", "atom_ids"),
        ("SLEUTH_KB_NODE_IDS", "node_ids"),
        ("SLEUTH_KB_QA_SEARCH_MODE", "qa_search_mode"),
        ("SLEUTH_KB_TIME_FILTER_START_TIME", "time_filter_start_time"),
        ("SLEUTH_KB_TIME_FILTER_END_TIME", "time_filter_end_time"),
        ("SLEUTH_KB_TAG_NAME", "tag_name"),
        ("SLEUTH_KB_TAG_VALUE_IDS", "tag_value_ids"),
        ("SLEUTH_KB_TAG_VALUE_NAMES", "tag_value_names"),
        ("SLEUTH_KB_TAG_SEARCH_OPERATION", "tag_search_operation"),
        ("SLEUTH_KB_SUBNET_TYPE", "subnet_type"),
    ):
        val = os.environ.get(env_key)
        if val:
            setattr(cfg.kb, attr, val)
    kb_timeout = _env_float("SLEUTH_KB_API_TIMEOUT")
    if kb_timeout is not None:
        cfg.kb.api_timeout = kb_timeout
    kb_sort = _env_int("SLEUTH_KB_SORT_COUNT")
    if kb_sort is not None:
        cfg.kb.sort_count = kb_sort
    kb_score = _env_float("SLEUTH_KB_SORT_SCORE")
    if kb_score is not None:
        cfg.kb.sort_score = kb_score
    kb_recall = _env_int("SLEUTH_KB_RECALL_COUNT")
    if kb_recall is not None:
        cfg.kb.recall_count = kb_recall
    kb_day = _env_int("SLEUTH_KB_TIME_FILTER_BY_DAY")
    if kb_day is not None:
        cfg.kb.time_filter_by_day = kb_day
    kb_combine = _env_bool("SLEUTH_KB_TIME_COMBINE")
    if kb_combine is not None:
        cfg.kb.time_combine = kb_combine
    kb_html = _env_bool("SLEUTH_KB_HTML_CLEAR")
    if kb_html is not None:
        cfg.kb.html_clear = kb_html
    kb_tf = _env_bool("SLEUTH_KB_TIME_FILTER_ENABLE")
    if kb_tf is not None:
        cfg.kb.time_filter_enable = kb_tf

    # long-term memory (OpenGauss) + identity ACL (session DB)
    if os.environ.get("SLEUTH_MEMORY_BACKEND") is not None:
        cfg.memory.backend = (os.environ.get("SLEUTH_MEMORY_BACKEND") or "").strip()
    for env_key, attr in (
        ("SLEUTH_OG_HOST", "og_host"),
        ("SLEUTH_OG_USER", "og_user"),
        ("SLEUTH_OG_PASSWORD", "og_password"),
        ("SLEUTH_OG_DATABASE", "og_database"),
        ("SLEUTH_OG_DSN", "og_dsn"),
        ("SLEUTH_OG_SCHEMA", "og_schema"),
        ("SLEUTH_MEMORY_TABLE_ITEM", "table_item"),
        ("SLEUTH_MEMORY_TABLE_AUDIT", "table_audit"),
        ("SLEUTH_EMBEDDING_MODEL", "embedding_model"),
        ("SLEUTH_EMBEDDING_BASE_URL", "embedding_base_url"),
        ("SLEUTH_EMBEDDING_API_KEY", "embedding_api_key"),
        ("SLEUTH_MEMORY_MIN_SCORE", "min_score"),
        ("SLEUTH_MEMORY_SCENARIOS", "scenarios"),
        ("SLEUTH_MEMORY_KINDS", "kinds"),
        ("SLEUTH_MEMORY_PIN_KINDS", "pin_kinds"),
        ("SLEUTH_MEMORY_VECTOR_KIND", "vector_kind"),
        ("SLEUTH_MEMORY_TTL_KINDS", "ttl_kinds"),
        ("SLEUTH_MEMORY_SCOPE_KINDS", "scope_kinds"),
        ("SLEUTH_MEMORY_ORIGINS", "origins"),
    ):
        val = os.environ.get(env_key)
        if val:
            setattr(cfg.memory, attr, val)
    og_port = _env_int("SLEUTH_OG_PORT")
    if og_port is not None:
        cfg.memory.og_port = og_port
    og_timeout = _env_float("SLEUTH_OG_CONNECT_TIMEOUT_S")
    if og_timeout is not None:
        cfg.memory.og_connect_timeout_s = og_timeout
    emb_dim = _env_int("SLEUTH_EMBEDDING_DIM")
    if emb_dim is not None:
        cfg.memory.embedding_dim = emb_dim
    top_k = _env_int("SLEUTH_MEMORY_TOP_K")
    if top_k is not None:
        cfg.memory.top_k = top_k
    max_items = _env_int("SLEUTH_MEMORY_MAX_ITEMS")
    if max_items is not None:
        cfg.memory.max_items = max_items
    max_chars = _env_int("SLEUTH_MEMORY_MAX_CHARS")
    if max_chars is not None:
        cfg.memory.max_chars = max_chars
    ttl_days = _env_int("SLEUTH_MEMORY_PATTERN_TTL_DAYS")
    if ttl_days is not None:
        cfg.memory.pattern_ttl_days = ttl_days

    acl_on = _env_bool("SLEUTH_ACL_ENABLED")
    if acl_on is not None:
        cfg.acl.enabled = acl_on
    acl_open = _env_bool("SLEUTH_ACL_DEFAULT_AGENT_OPEN")
    if acl_open is not None:
        cfg.acl.default_agent_open = acl_open
    if os.environ.get("SLEUTH_ACL_DEFAULT_AGENT_NAME") is not None:
        cfg.acl.default_agent_name = os.environ.get("SLEUTH_ACL_DEFAULT_AGENT_NAME") or ""
    for env_key, attr in (
        ("SLEUTH_ACL_TABLE_ORG", "table_org"),
        ("SLEUTH_ACL_TABLE_ROLE", "table_role"),
        ("SLEUTH_ACL_TABLE_USER", "table_user"),
        ("SLEUTH_ACL_TABLE_GRANT", "table_grant"),
    ):
        val = os.environ.get(env_key)
        if val:
            setattr(cfg.acl, attr, val)

    # skills
    refresh = _env_int("SLEUTH_SKILLS_REFRESH_SECONDS")
    if refresh is not None:
        cfg.skills.refresh_seconds = refresh
    for p in _env_csv("SLEUTH_SKILLS_PATHS"):
        if p not in cfg.skills.paths:
            cfg.skills.paths.append(p)
    for u in _env_csv("SLEUTH_SKILLS_URLS"):
        if u not in cfg.skills.urls:
            cfg.skills.urls.append(u)
    s3_json = os.environ.get("SLEUTH_SKILLS_S3")
    if s3_json:
        try:
            data = json.loads(s3_json)
            if isinstance(data, list):
                for item in data:
                    entry = _parse_s3_entry(item)
                    if entry is not None:
                        cfg.skills.s3.append(entry)
        except json.JSONDecodeError:
            # treat as comma-separated s3:// uris
            for uri in _env_csv("SLEUTH_SKILLS_S3"):
                cfg.skills.s3.append(SkillS3Entry(uri=uri))

    _apply_shared_object_store(cfg)

    # MCP servers as JSON
    mcp_json = os.environ.get("SLEUTH_MCP_SERVERS")
    if mcp_json:
        try:
            data = json.loads(mcp_json)
            if isinstance(data, dict):
                cfg.merge({"mcp": {"servers": data}})
        except json.JSONDecodeError:
            pass

    startup = _env_int("SLEUTH_MCP_TIMEOUT_STARTUP")
    if startup is not None:
        cfg.mcp_timeout["startup"] = startup
    per_server = _env_int("SLEUTH_MCP_TIMEOUT_PER_SERVER")
    if per_server is not None:
        cfg.mcp_timeout["per_server"] = per_server
    request = _env_int("SLEUTH_MCP_TIMEOUT_REQUEST")
    if request is not None:
        cfg.mcp_timeout["request"] = request
    retry_s = _env_int("SLEUTH_MCP_RETRY_SECONDS")
    if retry_s is not None:
        cfg.mcp_retry_seconds = retry_s

    # Multi-provider credentials: SLEUTH_PROVIDERS JSON
    # {"deepseek":{"apiKey":"sk-...","baseURL":"https://..."}, "qwen":{...}}
    providers_json = os.environ.get("SLEUTH_PROVIDERS")
    if providers_json:
        try:
            data = json.loads(providers_json)
            if isinstance(data, dict):
                _merge_providers_env(cfg, data)
        except json.JSONDecodeError:
            pass

    # provider options shortcuts (single-provider env vars)
    for env_key, provider_id in (
        ("OPENAI_API_KEY", "openai"),
        ("OPENAI_BASE_URL", "openai"),
        ("OPENROUTER_API_KEY", "openrouter"),
        ("OPENROUTER_BASE_URL", "openrouter"),
    ):
        if env_key.endswith("_API_KEY") and os.environ.get(env_key):
            opts = cfg.providers.setdefault(provider_id, {}).setdefault("options", {})
            opts.setdefault("apiKey", os.environ[env_key])
        if env_key.endswith("_BASE_URL") and os.environ.get(env_key):
            opts = cfg.providers.setdefault(provider_id, {}).setdefault("options", {})
            opts.setdefault("baseURL", os.environ[env_key])


def _merge_providers_env(cfg: Config, data: Dict[str, Any]) -> None:
    """Merge SLEUTH_PROVIDERS entries into cfg.providers.*.options."""
    for pid, entry in data.items():
        if not pid or not isinstance(entry, dict):
            continue
        opts = cfg.providers.setdefault(str(pid), {}).setdefault("options", {})
        nested = entry.get("options") if isinstance(entry.get("options"), dict) else None
        src = nested or entry
        if src.get("apiKey") or src.get("api_key"):
            opts["apiKey"] = str(src.get("apiKey") or src.get("api_key"))
        if src.get("baseURL") or src.get("base_url"):
            opts["baseURL"] = str(src.get("baseURL") or src.get("base_url"))


def load(cwd: Optional[Path] = None) -> Config:
    """Load config: dotenv already applied by CLI; merge files then env wins."""
    cfg = Config()
    cwd = cwd or Path.cwd()

    gdir = _global_config_dir()
    for name in _CONFIG_FILENAMES:
        p = gdir / name
        if p.is_file():
            cfg.merge(_load_jsonc(p))
            break

    for p in _walk_project_configs(cwd):
        cfg.merge(_load_jsonc(p))

    for d in _opencode_dirs(cwd):
        _load_markdown_agents(d, cfg)
        _load_markdown_commands(d, cfg)

    inline = os.environ.get("OPENCODE_CONFIG_CONTENT") or os.environ.get("SLEUTH_CONFIG_CONTENT")
    if inline:
        try:
            cfg.merge(json.loads(inline))
        except json.JSONDecodeError:
            pass

    explicit = os.environ.get("OPENCODE_CONFIG") or os.environ.get("SLEUTH_CONFIG")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            cfg.merge(_load_jsonc(p))

    # .env / process env applied last so they win over JSONC
    _apply_env(cfg)
    return cfg


def parse_model_ref(ref: str) -> tuple[str, str]:
    if "/" not in ref:
        return "openai", ref
    provider, _, model = ref.partition("/")
    return provider, model
