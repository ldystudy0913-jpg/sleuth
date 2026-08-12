"""Shared catalog payloads for HTTP API and CLI slash commands."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


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
    cfg, *, include_hidden: bool = False, mcp_manager=None
) -> Dict[str, Any]:
    names = set(cfg.agents)
    if cfg.default_agent:
        names.add(cfg.default_agent)
    card_servers = {}
    if mcp_manager is not None:
        card_servers = dict(getattr(mcp_manager, "agent_card_servers", {}) or {})
        for n in getattr(mcp_manager, "agent_cards", {}) or {}:
            names.add(n)

    agents = []
    for name in sorted(names):
        ag = cfg.agent(name)
        if ag.hidden and not include_hidden:
            continue
        mcp_server = card_servers.get(name) or ""
        if mcp_server and mcp_manager is not None:
            available = bool(mcp_manager.is_server_connected(mcp_server))
            source = "mcp"
        else:
            available = True
            source = "local"
        agents.append(
            {
                "name": ag.name or name,
                "description": ag.description,
                "mode": ag.mode,
                "hidden": bool(ag.hidden),
                "model": ag.model,
                "source": source,
                "mcp_server": mcp_server or None,
                "available": available,
            }
        )
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


def skills_payload(cfg, workdir: Optional[Path] = None) -> List[Dict[str, Any]]:
    from .skill import ensure_skills_fresh

    workdir = workdir or Path.cwd()
    skills = ensure_skills_fresh(cfg, workdir)
    return [
        {"name": s.name, "description": s.description, "location": str(s.location)}
        for s in skills.values()
    ]
