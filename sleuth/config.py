"""Configuration loading and merging.

opencode discovers config from many places (global ~/.config/opencode,
project tree opencode.jsonc, .opencode/ dirs, env vars) and deep-merges
them. We port the essential shape: a single Config dataclass plus a loader
that walks from the cwd up to the worktree root, merging each found file on
top of the global one.

Supported top-level keys (a deliberate subset of opencode's schema):
  model            "provider/model" e.g. "openai/gpt-4o"
  small_model      model used for title/summary tasks
  default_agent    "build" | "plan"
  agent            { build: {prompt, model, permission, steps}, plan: {...} }
  provider         { openai: {options: {apiKey, baseURL}}, openrouter: {...}, ... }
  permission       { edit: "allow"|"ask"|"deny", bash: ..., ... }
  instructions     [str, ...]   extra system-prompt lines / globs / http(s) URLs
  tool_output      { max_lines, max_bytes }
  max_steps        hard cap on agentic iterations
  subagent_depth   max nested task depth (default 1)
  context_limit    model context window for compaction (default 128000)
  compaction       { auto: bool, reserved: int }
  command          { name: { template, description, agent } } — also loaded from
                   .opencode/command/*.md

The model, api key, and base url can all live in .env instead — see
provider/factory.py: OPENCODE_MODEL, <PROVIDER>_API_KEY, <PROVIDER>_BASE_URL.
Precedence: --model flag > opencode.json > .env (OPENCODE_MODEL) for the
model; config options > .env > SDK default for keys/base_url.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    name: str = "build"
    prompt: Optional[str] = None
    model: Optional[str] = None
    steps: int = 50
    permission: Dict[str, Any] = field(default_factory=dict)
    description: Optional[str] = None
    mode: str = "all"  # "primary" | "subagent" | "all"
    hidden: bool = False

    def merge(self, other: Dict[str, Any]) -> "AgentConfig":
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
class Config:
    model: Optional[str] = None
    small_model: Optional[str] = None
    default_agent: str = "build"
    agents: Dict[str, AgentConfig] = field(default_factory=dict)
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

    def agent(self, name: Optional[str] = None) -> AgentConfig:
        name = name or self.default_agent
        return self.agents.get(name, AgentConfig(name=name))

    def provider_options(self, provider_id: str) -> Dict[str, Any]:
        return self.providers.get(provider_id, {}).get("options", {})

    # deep-merge a raw dict on top of self
    def merge(self, raw: Dict[str, Any]) -> "Config":
        if "model" in raw and raw["model"]:
            self.model = raw["model"]
        if "small_model" in raw and raw["small_model"]:
            self.small_model = raw["small_model"]
        if "default_agent" in raw and raw["default_agent"]:
            self.default_agent = raw["default_agent"]
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
        return self


# ---------------------------------------------------------------------------
# discovery + loading
# ---------------------------------------------------------------------------


def _strip_comments(text: str) -> str:
    """Crude JSONC comment stripper (// line and /* block */). Good enough
    for hand-edited config files; not a full parser."""
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
    # XDG_CONFIG_HOME on posix, %APPDATA% on windows, else ~/.config
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
    """Walk *up* from cwd collecting opencode config files.

    Stops at the git worktree root (or filesystem root) like opencode does.
    """
    found: List[Path] = []
    root = _git_root(cwd) or _fs_root(cwd)
    cur = cwd.resolve()
    while True:
        for name in _CONFIG_FILENAMES:
            p = cur / name
            if p.is_file():
                found.append(p)
        # also .opencode/opencode.jsonc
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
    """Port of opencode `config/agent.ts` — `{agent,agents}/**/*.md`."""
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
    """Port of opencode `config/command.ts` — `{command,commands}/**/*.md`."""
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
    """Global + project `.opencode` dirs (and cwd itself for agent/command)."""
    dirs: List[Path] = []
    gdir = _global_config_dir()
    if gdir.is_dir():
        dirs.append(gdir)
    # walk up like configs
    root = _git_root(cwd) or _fs_root(cwd)
    cur = cwd.resolve()
    found: List[Path] = []
    while True:
        for name in (".opencode",):
            p = cur / name
            if p.is_dir():
                found.append(p)
        if cur == root or cur == cur.parent:
            break
        cur = cur.parent
    # cwd-nearest last so it wins when merged
    dirs.extend(reversed(found))
    return dirs


def load(cwd: Optional[Path] = None) -> Config:
    """Load merged config from global + project sources + env override."""
    cfg = Config()
    cwd = cwd or Path.cwd()

    # 1. global
    gdir = _global_config_dir()
    for name in _CONFIG_FILENAMES:
        p = gdir / name
        if p.is_file():
            cfg.merge(_load_jsonc(p))
            break

    # 2. project tree (lowest-to-highest precedence as we walk up; later
    #    files in the list override earlier, and the cwd file wins last)
    for p in _walk_project_configs(cwd):
        cfg.merge(_load_jsonc(p))

    # 3. markdown agents/commands from global + .opencode dirs
    for d in _opencode_dirs(cwd):
        _load_markdown_agents(d, cfg)
        _load_markdown_commands(d, cfg)

    # 4. env override: inline JSON content
    inline = os.environ.get("OPENCODE_CONFIG_CONTENT")
    if inline:
        try:
            cfg.merge(json.loads(inline))
        except json.JSONDecodeError:
            pass

    # 5. env var: explicit file path (highest precedence)
    explicit = os.environ.get("OPENCODE_CONFIG")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            cfg.merge(_load_jsonc(p))

    # 6. context limit from env
    env_ctx = os.environ.get("OPENCODE_CONTEXT_LIMIT")
    if env_ctx:
        try:
            cfg.context_limit = int(env_ctx)
        except ValueError:
            pass

    return cfg


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split "provider/model" into (provider_id, model_id)."""
    if "/" not in ref:
        # default to openai if a bare model id is given
        return "openai", ref
    provider, _, model = ref.partition("/")
    return provider, model
