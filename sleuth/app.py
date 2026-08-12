"""Shared session / registry assembly for CLI and HTTP server."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .agent import ruleset_for
from .config import Config, load
from .permission import Permission, allow_all_rules, from_config as permission_from_config
from .provider.factory import resolve_model
from .session import NullRenderer, Session
from .skill import ensure_skills_fresh, get_skills, refresh_skills, set_skills
from .storage.factory import create_store
from .tools.registry import ToolRegistry


def _apply_mcp_cards(config: Config, mcp_manager) -> None:
    from .mcp import apply_agent_cards_to_config

    if not mcp_manager or not mcp_manager.agent_cards:
        return
    card_skills = apply_agent_cards_to_config(
        config,
        mcp_manager.agent_cards,
        server_by_agent=mcp_manager.agent_card_servers,
    )
    if card_skills:
        merged = dict(get_skills())
        for sk in card_skills:
            if sk.name not in merged:
                merged[sk.name] = sk
        set_skills(merged)


def build_registry(config: Config, *, workdir: Optional[Path] = None, renderer=None):
    from .mcp import bridge_tools, get_manager

    workdir = workdir or Path.cwd()
    registry = ToolRegistry()
    skills = ensure_skills_fresh(config, workdir)
    set_skills(skills)

    mcp_manager = None
    try:
        mcp_manager = get_manager(config)
        for err in mcp_manager.errors:
            if renderer is not None:
                renderer.on_error(err)
        registry.register_many(bridge_tools(mcp_manager))
        _apply_mcp_cards(config, mcp_manager)
    except Exception as exc:
        if renderer is not None:
            renderer.on_error(f"mcp init failed: {exc}")

    return registry, mcp_manager


def reload_mcp(config: Optional[Config] = None, workdir: Optional[Path] = None) -> Dict[str, Any]:
    """Hot-reload MCP connections, tools, and agent cards."""
    from .mcp import get_manager

    workdir = workdir or Path.cwd()
    config = config or load(workdir)
    mgr = get_manager(config)
    mgr.reload(config)
    _apply_mcp_cards(config, mgr)
    return {
        "ok": True,
        "errors": list(mgr.errors),
        "tools": sorted(mgr.tools.keys()),
        "agents": sorted(mgr.agent_cards.keys()),
        "servers": [
            {
                "name": s.name,
                "url": s.url,
                "connected": s.connected,
                "error": s.error,
                "agent": s.agent,
            }
            for s in mgr.server_statuses()
        ],
    }


def resync_session_mcp(session: Session) -> Dict[str, Any]:
    """Reload MCP and refresh tools on a live Session (CLI long-lived process)."""
    from .mcp import bridge_tools, get_manager

    result = reload_mcp(session.config, session.workdir)
    mgr = get_manager(session.config)
    prev = getattr(session, "_mcp_tool_names", set()) or set()
    for name in prev:
        session.registry._tools.pop(name, None)
    tools = bridge_tools(mgr)
    session.registry.register_many(tools)
    session._mcp_tool_names = {t.name for t in tools}
    session._mcp_manager = mgr
    return result


def build_permission(config: Config, agent_name: str, *, yolo: bool = False) -> Permission:
    if yolo:
        return Permission(rules=allow_all_rules())
    rules = ruleset_for(agent_name)
    agent_cfg = config.agent(agent_name)
    if agent_cfg.permission:
        rules = rules + permission_from_config(agent_cfg.permission)
    if config.permission:
        rules = rules + permission_from_config(config.permission)
    return Permission(rules=rules)


def build_session(
    *,
    config: Optional[Config] = None,
    workdir: Optional[Path] = None,
    agent_name: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    continue_latest: bool = False,
    yolo: bool = False,
    renderer=None,
    store=None,
    prefer_agent: Optional[str] = None,
) -> Session:
    workdir = workdir or Path.cwd()
    config = config or load(workdir)
    if user_id:
        config.user_id = user_id
    requested_agent = (prefer_agent or agent_name or config.default_agent or "build").strip()
    provider, model_id = resolve_model(config, requested_agent)

    if store is None:
        store = create_store(config)
    renderer = renderer or NullRenderer()
    registry, mcp_manager = build_registry(config, workdir=workdir, renderer=renderer)

    permission = build_permission(config, requested_agent, yolo=yolo)

    def _attach(sess: Session) -> Session:
        sess._mcp_manager = mcp_manager
        sess.user_id = config.user_id or "local"
        sess.yolo = bool(yolo)
        if mcp_manager is not None:
            sess._mcp_tool_names = set(mcp_manager.tools.keys())
        else:
            sess._mcp_tool_names = set()
        return sess

    if session_id:
        sess = _attach(
            Session.load(
                provider=provider,
                registry=registry,
                config=config,
                workdir=workdir,
                permission=permission,
                store=store,
                session_id_value=session_id,
                agent_name=requested_agent,
                model_id=model_id,
                renderer=renderer,
                prefer_agent=prefer_agent,
            )
        )
        if prefer_agent:
            if sess.agent_name != prefer_agent:
                sess.set_agent(prefer_agent, yolo=yolo)
            else:
                sess.permission = build_permission(config, prefer_agent, yolo=yolo)
                try:
                    sess._update_record()
                except Exception:
                    pass
        return sess

    if continue_latest:
        recent = store.list_sessions(
            directory=str(workdir), user_id=config.user_id, limit=1
        )
        if recent:
            return _attach(
                Session.load(
                    provider=provider,
                    registry=registry,
                    config=config,
                    workdir=workdir,
                    permission=permission,
                    store=store,
                    session_id_value=recent[0].id,
                    agent_name=requested_agent,
                    model_id=model_id,
                    renderer=renderer,
                    prefer_agent=prefer_agent,
                )
            )

    session = Session(
        provider=provider,
        registry=registry,
        config=config,
        workdir=workdir,
        permission=permission,
        agent_name=requested_agent,
        model_id=model_id,
        renderer=renderer,
        store=store,
        user_id=config.user_id or "local",
    )
    return _attach(session)


def reload_skills(config: Optional[Config] = None, workdir: Optional[Path] = None):
    return refresh_skills(config, workdir, force=True)
