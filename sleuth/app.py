"""Shared session / registry assembly for CLI and HTTP server."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agent import ruleset_for
from .config import Config, load
from .permission import Permission, allow_all_rules, from_config as permission_from_config
from .provider.factory import resolve_model
from .session import NullRenderer, Session
from .skill import ensure_skills_fresh, refresh_skills, set_skills
from .storage.factory import create_store
from .tools.registry import ToolRegistry


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
    except Exception as exc:
        if renderer is not None:
            renderer.on_error(f"mcp init failed: {exc}")

    return registry, mcp_manager


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
) -> Session:
    workdir = workdir or Path.cwd()
    config = config or load(workdir)
    if user_id:
        config.user_id = user_id
    agent_name = agent_name or config.default_agent
    provider, model_id = resolve_model(config, agent_name)

    if yolo:
        rules = allow_all_rules()
    else:
        rules = ruleset_for(agent_name)
        agent_cfg = config.agent(agent_name)
        if agent_cfg.permission:
            rules = rules + permission_from_config(agent_cfg.permission)
        if config.permission:
            rules = rules + permission_from_config(config.permission)

    permission = Permission(rules=rules)
    if store is None:
        store = create_store(config)
    renderer = renderer or NullRenderer()
    registry, mcp_manager = build_registry(config, workdir=workdir, renderer=renderer)

    def _attach(sess: Session) -> Session:
        sess._mcp_manager = mcp_manager
        sess.user_id = config.user_id or "local"
        return sess

    if session_id:
        return _attach(
            Session.load(
                provider=provider,
                registry=registry,
                config=config,
                workdir=workdir,
                permission=permission,
                store=store,
                session_id_value=session_id,
                agent_name=agent_name,
                model_id=model_id,
                renderer=renderer,
            )
        )

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
                    agent_name=agent_name,
                    model_id=model_id,
                    renderer=renderer,
                )
            )

    session = Session(
        provider=provider,
        registry=registry,
        config=config,
        workdir=workdir,
        permission=permission,
        agent_name=agent_name,
        model_id=model_id,
        renderer=renderer,
        store=store,
        user_id=config.user_id or "local",
    )
    return _attach(session)


def reload_skills(config: Optional[Config] = None, workdir: Optional[Path] = None):
    return refresh_skills(config, workdir, force=True)
