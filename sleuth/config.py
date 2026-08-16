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
