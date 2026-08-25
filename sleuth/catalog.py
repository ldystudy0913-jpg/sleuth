"""Shared catalog payloads for HTTP API and CLI slash commands."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import normalize_agent_key


def _apply_live_mcp_cards(cfg, mcp_manager) -> None:
    """Fill config + skill catalog from cards the manager already fetched."""
    cards = getattr(mcp_manager, "agent_cards", None) or {}
    if not cards:
        return
    from .mcp import apply_agent_cards_to_config
    from .skill import get_skills, set_skills

    card_skills = apply_agent_cards_to_config(
        cfg,
        cards,
        server_by_agent=getattr(mcp_manager, "agent_card_servers", None) or {},
    )
    if not card_skills:
        return
    merged = dict(get_skills())
    for sk in card_skills:
        if sk.name not in merged:
            merged[sk.name] = sk
    set_skills(merged)


# Built-in agent ids → UI title when agent.md / jsonc did not set one.
_BUILTIN_TITLES = {
    "build": "通用助手",
}


def _agent_title(ag, name: str) -> str:
    custom = (getattr(ag, "title", None) or "").strip()
    if custom:
        return custom
    key = (getattr(ag, "name", None) or name or "").strip()
    return _BUILTIN_TITLES.get(key) or key or name


def catalog_model_ref(cfg, alias: str) -> str:
    """Resolve catalog alias to provider/model without seeding credentials."""
    key = (alias or "").strip()
    if not key:
        return key
    entry = cfg.models.get(key)
    if entry is None:
        return key
    if isinstance(entry, str):
        return entry.strip() or key
    if isinstance(entry, dict):
        model_id = str(entry.get("model") or entry.get("id") or key).strip()
        provider_id = str(entry.get("provider") or key).strip() or key
        return f"{provider_id}/{model_id}"
    return key


def models_payload(cfg) -> Dict[str, Any]:
    models = [
        {
            "id": alias,
            "ref": catalog_model_ref(cfg, alias),
            "label": cfg.model_entry_label(alias),
        }
        for alias in sorted(cfg.models)
    ]
    return {"default": cfg.model, "models": models}


def agents_payload(
    cfg, *, include_hidden: bool = False, mcp_manager=None, user_id: Optional[str] = None
) -> Dict[str, Any]:
    names = set(cfg.agents)
    if cfg.default_agent:
        names.add(cfg.default_agent)
    card_servers = {}
    if mcp_manager is not None:
        card_servers = dict(getattr(mcp_manager, "agent_card_servers", {}) or {})
        cards = getattr(mcp_manager, "agent_cards", {}) or {}
        for n in cards:
            names.add(n)
        if cards:
            _apply_live_mcp_cards(cfg, mcp_manager)

    agents = []
    seen = set()
    for name in sorted(names):
        ag = cfg.agent(name)
        if ag.hidden and not include_hidden:
            continue
        canon = ag.name or name
        if canon in seen:
            continue
        seen.add(canon)
        mcp_server = card_servers.get(name) or card_servers.get(ag.name or name) or ""
        if not mcp_server:
            for alias, canon in (getattr(cfg, "agent_aliases", None) or {}).items():
                if canon == (ag.name or name) and alias in (getattr(cfg, "mcp_servers", None) or {}):
                    mcp_server = alias
                    break
        if mcp_server and mcp_manager is not None:
            available = bool(mcp_manager.is_server_connected(mcp_server))
            source = "mcp"
        else:
            available = True
            source = "local"
        canon = ag.name or name
        aliases = sorted(
            {
                a
                for a, c in (getattr(cfg, "agent_aliases", None) or {}).items()
                if c == canon and a != canon and a != normalize_agent_key(canon)
            }
        )
        if mcp_server and mcp_server not in aliases and mcp_server != canon:
            aliases.append(mcp_server)
        agents.append(
            {
                "name": canon,
                "title": _agent_title(ag, name),
                "description": ag.description,
                "mode": ag.mode,
                "hidden": bool(ag.hidden),
                "model": ag.model,
                "source": source,
                "mcp_server": mcp_server or None,
                "aliases": aliases,
                "available": available,
            }
        )
    if user_id:
        from .memory.acl import filter_resources

        agents = filter_resources(cfg, user_id, "agent", agents, id_key="name")
    return {"default": cfg.default_agent, "agents": agents}


def mcp_status_dict(cfg) -> Dict[str, Any]:
    try:
        from .mcp import get_manager

        mgr = get_manager(cfg)
        servers = [
            {
                "name": s.name,
                "url": s.url,
                "connected": s.connected,
                "error": s.error,
                "agent": s.agent,
                "agents": [
                    a
                    for a, srv in mgr.agent_card_servers.items()
                    if srv == s.name
                ],
            }
            for s in mgr.server_statuses()
        ]
        return {
            "servers": servers,
            "tools": sorted(mgr.tools.keys()),
            "agents": sorted(mgr.agent_cards.keys()),
            "errors": list(mgr.errors),
        }
    except Exception as exc:
        return {
            "servers": [],
            "tools": [],
            "agents": [],
            "errors": [str(exc)],
        }


def skills_payload(
    cfg, workdir: Optional[Path] = None, *, user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    from .skill import ensure_skills_fresh

    workdir = workdir or Path.cwd()
    skills = ensure_skills_fresh(cfg, workdir)
    rows = [
        {"name": s.name, "description": s.description, "location": str(s.location)}
        for s in skills.values()
    ]
    if user_id:
        from .memory.acl import filter_resources

        rows = filter_resources(cfg, user_id, "skill", rows, id_key="name")
    return rows
