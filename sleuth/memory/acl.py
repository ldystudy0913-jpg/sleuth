"""Role-centric agent/skill grants. Missing tables degrade to today's all-visible."""
from __future__ import annotations

from typing import Iterable, List, Optional

from . import settings
from .directory import Directory, directory_for


def _identity(config, user_id: str, directory: Optional[Directory] = None):
    directory = directory or directory_for(config)
    rec = directory.get_user(user_id) if user_id else None
    active = settings.acl_active(config)
    if rec is None or (rec.row_status and rec.row_status != active):
        return None, None
    role_id = rec.role_id or None
    org_id = rec.org_id or None
    if role_id:
        role = directory.get_role(role_id)
        if role is None or (role.row_status and role.row_status != active):
            role_id = None
    if org_id:
        org = directory.get_org(org_id)
        if org is None or (org.row_status and org.row_status != active):
            org_id = None
    return role_id, org_id


def resolve_identity(config, user_id: str, directory: Optional[Directory] = None):
    """Return (role_id, org_id) for X-User-Id. Both may be None."""
    return _identity(config, user_id, directory)


def attach_identity(session) -> None:
    config = getattr(session, "config", None)
    user_id = getattr(session, "user_id", None) or ""
    if config is None:
        session.role_id = None
        session.org_id = None
        return
    try:
        role_id, org_id = resolve_identity(config, user_id)
    except Exception:
        role_id, org_id = None, None
    session.role_id = role_id
    session.org_id = org_id


def resource_allowed(
    config,
    user_id: str,
    resource_kind: str,
    resource_id: str,
    *,
    directory: Optional[Directory] = None,
) -> bool:
    if not settings.acl_enabled(config):
        return True
    directory = directory or directory_for(config)
    if not directory.available():
        return True
    resource_id = (resource_id or "").strip()
    resource_kind = (resource_kind or "").strip()
    if not resource_id or not resource_kind:
        return False
    if resource_kind == "agent":
        resolver = getattr(config, "resolve_agent_name", None)
        if callable(resolver):
            resource_id = resolver(resource_id)
    allow = settings.grant_allow(config)
    deny = settings.grant_deny(config)
    grants = directory.list_grants(
        resource_kind=resource_kind, resource_id=resource_id, active_only=True
    )
    user_grants = [g for g in grants if g.scope_kind == "user" and g.scope_id == user_id]
    if any(g.grant_effect == deny for g in user_grants):
        return False
    if any(g.grant_effect == allow for g in user_grants):
        return True
    role_id, org_id = _identity(config, user_id, directory)
    if role_id and any(
        g.scope_kind == "role" and g.scope_id == role_id and g.grant_effect == allow
        for g in grants
    ):
        return True
    if org_id and any(
        g.scope_kind == "org" and g.scope_id == org_id and g.grant_effect == allow
        for g in grants
    ):
        return True
    if resource_kind == "agent" and settings.default_agent_open(config):
        if resource_id == settings.default_agent_name(config):
            return True
    return False


def assert_resource_allowed(
    config,
    user_id: str,
    resource_kind: str,
    resource_id: str,
    *,
    directory: Optional[Directory] = None,
) -> None:
    if resource_allowed(config, user_id, resource_kind, resource_id, directory=directory):
        return
    raise ValueError(f"{resource_kind} not authorized: {resource_id}")


def filter_resources(
    config,
    user_id: str,
    resource_kind: str,
    rows: Iterable[dict],
    *,
    id_key: str = "name",
    directory: Optional[Directory] = None,
) -> List[dict]:
    if not settings.acl_enabled(config):
        return list(rows)
    directory = directory or directory_for(config)
    if not directory.available():
        return list(rows)
    out = []
    for row in rows:
        rid = str((row or {}).get(id_key) or "").strip()
        if rid and resource_allowed(
            config, user_id, resource_kind, rid, directory=directory
        ):
            out.append(row)
    return out


def session_acl_error(session) -> Optional[str]:
    """If the current agent is not allowed, return an error string."""
    config = getattr(session, "config", None)
    if config is None or not settings.acl_enabled(config):
        return None
    user_id = getattr(session, "user_id", None) or ""
    agent = getattr(session, "agent_name", None) or ""
    if resource_allowed(config, user_id, "agent", agent):
        for skill in getattr(session, "skill_names", None) or []:
            if not resource_allowed(config, user_id, "skill", skill):
                return f"skill not authorized: {skill}"
        return None
    return f"agent not authorized: {agent}"
