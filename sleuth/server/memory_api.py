"""HTTP handlers for memory and admin directory/grants."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..memory import settings
from ..memory.acl import resolve_identity
from ..memory.directory import directory_for
from ..memory.models import GrantRecord, UserRecord
from ..memory.service import (
    MemoryPrivacyError,
    MemoryUnavailable,
    can_access_item,
    forget_memory,
    search_for_user,
    write_memory,
)
from ..memory.store import memory_store_for


def _json(data, status=200):
    from starlette.responses import JSONResponse

    return JSONResponse(data, status_code=status)


def _is_admin(request, config) -> bool:
    token = getattr(getattr(config, "server", None), "admin_token", None) or ""
    if not token:
        return True
    return (request.headers.get("x-admin-token") or "") == token


def _require_admin(request, config):
    if _is_admin(request, config):
        return None
    return _json({"error": "unauthorized"}, 401)


def _user_id(request) -> str:
    return (
        request.headers.get("x-user-id")
        or request.query_params.get("user_id")
        or "anonymous"
    )


def _memory_unavailable(config=None):
    body = {"error": "long-term memory is not configured"}
    detail = (getattr(config, "_memory_error", None) or "").strip() if config is not None else ""
    if detail:
        body["detail"] = detail
    return _json(body, 503)


async def list_or_search_memory(request, config):
    user_id = _user_id(request)
    q = (request.query_params.get("q") or "").strip()
    store = memory_store_for(config)
    if store is None:
        return _memory_unavailable(config)
    try:
        if q:
            items = search_for_user(config, user_id, q)
        else:
            from ..memory.resolve import identity_scopes

            items = store.list_scope(identity_scopes(config, user_id), include_inactive=False)
    except MemoryUnavailable:
        return _memory_unavailable(config)
    except Exception as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"items": [item.to_public_dict() for item in items]})


async def create_memory(request, config):
    user_id = _user_id(request)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "invalid json"}, 400)
    admin = _is_admin(request, config)
    scope_kind = str(body.get("scope_kind") or "user").strip()
    scope_id = str(body.get("scope_id") or user_id).strip()
    if scope_kind != "user" or scope_id != user_id:
        if not admin:
            return _json({"error": "only admin can write role or org memory"}, 401)
        denied = _require_admin(request, config)
        if denied is not None:
            return denied
    scenarios = settings.scenarios(config)
    kinds = settings.kinds(config)
    try:
        item = write_memory(
            config,
            actor=user_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            scenario_code=str(body.get("scenario_code") or (scenarios[0] if scenarios else "")),
            mem_kind=str(body.get("mem_kind") or (kinds[0] if kinds else "")),
            item_key=str(body.get("item_key") or ""),
            title_text=str(body.get("title_text") or ""),
            body_text=str(body.get("body_text") or ""),
            payload_text=body.get("payload_text"),
            importance_score=int(body.get("importance_score") or 3),
            confidence_score=str(body.get("confidence_score") or "1.0000"),
            origin_type=str(body.get("origin_type") or ("admin" if admin and scope_kind != "user" else "user_explicit")),
        )
    except MemoryPrivacyError as exc:
        return _json({"error": str(exc)}, 400)
    except MemoryUnavailable:
        return _memory_unavailable(config)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    except Exception as exc:
        return _json({"error": str(exc)}, 400)
    return _json(item.to_public_dict())


async def patch_memory(request, config):
    user_id = _user_id(request)
    item_id = request.path_params.get("memory_id") or ""
    store = memory_store_for(config)
    if store is None:
        return _memory_unavailable(config)
    item = store.get(item_id)
    if item is None:
        return _json({"error": "not found"}, 404)
    admin = _is_admin(request, config)
    if not admin and not can_access_item(config, user_id, item):
        return _json({"error": "not found"}, 404)
    if item.scope_kind != "user" and not admin:
        return _json({"error": "only admin can edit role or org memory"}, 401)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "invalid json"}, 400)
    title = str(body.get("title_text") if "title_text" in body else item.title_text)
    text = str(body.get("body_text") if "body_text" in body else item.body_text)
    payload = body.get("payload_text") if "payload_text" in body else item.payload_text
    try:
        updated = write_memory(
            config,
            actor=user_id,
            scope_kind=item.scope_kind,
            scope_id=item.scope_id,
            scenario_code=item.scenario_code,
            mem_kind=item.mem_kind,
            item_key=item.item_key,
            title_text=title,
            body_text=text,
            payload_text=payload,
            importance_score=int(body.get("importance_score") or item.importance_score),
            confidence_score=str(body.get("confidence_score") or item.confidence_score),
            origin_type=item.origin_type,
            expire_at=item.expire_at,
        )
    except MemoryPrivacyError as exc:
        return _json({"error": str(exc)}, 400)
    except MemoryUnavailable:
        return _memory_unavailable(config)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json(updated.to_public_dict())


async def delete_memory(request, config):
    user_id = _user_id(request)
    item_id = request.path_params.get("memory_id") or ""
    store = memory_store_for(config)
    if store is None:
        return _memory_unavailable(config)
    item = store.get(item_id)
    if item is None:
        return _json({"error": "not found"}, 404)
    admin = _is_admin(request, config)
    if not admin and not can_access_item(config, user_id, item):
        return _json({"error": "not found"}, 404)
    if item.scope_kind != "user" and not admin:
        return _json({"error": "only admin can forget role or org memory"}, 401)
    try:
        forgot = forget_memory(config, item_id, actor=user_id, store=store)
    except MemoryUnavailable:
        return _memory_unavailable(config)
    except ValueError as exc:
        return _json({"error": str(exc)}, 404)
    return _json({"ok": True, "id": forgot.id})


async def get_directory_user(request, config):
    denied = _require_admin(request, config)
    if denied is not None:
        return denied
    directory = directory_for(config)
    if not directory.available():
        return _json({"error": "directory tables are not available"}, 503)
    user_id = request.path_params.get("user_id") or ""
    rec = directory.get_user(user_id)
    if rec is None:
        return _json({"error": "not found"}, 404)
    return _json(
        {
            "user_id": rec.user_id,
            "display_name": rec.display_name,
            "role_id": rec.role_id,
            "org_id": rec.org_id,
            "row_status": rec.row_status,
        }
    )


async def put_directory_user(request, config):
    denied = _require_admin(request, config)
    if denied is not None:
        return denied
    directory = directory_for(config)
    if not directory.available():
        return _json({"error": "directory tables are not available"}, 503)
    user_id = request.path_params.get("user_id") or ""
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "invalid json"}, 400)
    rec = UserRecord(
        user_id=user_id,
        display_name=body.get("display_name"),
        role_id=body.get("role_id"),
        org_id=body.get("org_id"),
        row_status=str(body.get("row_status") or settings.acl_active(config)),
    )
    try:
        saved = directory.upsert_user(rec)
    except Exception as exc:
        return _json({"error": str(exc)}, 400)
    return _json(
        {
            "user_id": saved.user_id,
            "display_name": saved.display_name,
            "role_id": saved.role_id,
            "org_id": saved.org_id,
            "row_status": saved.row_status,
        }
    )


def _grant_payload(rec: GrantRecord) -> Dict[str, Any]:
    return {
        "grant_id": rec.grant_id,
        "scope_kind": rec.scope_kind,
        "scope_id": rec.scope_id,
        "resource_kind": rec.resource_kind,
        "resource_id": rec.resource_id,
        "grant_effect": rec.grant_effect,
        "row_status": rec.row_status,
    }


async def list_grants(request, config):
    denied = _require_admin(request, config)
    if denied is not None:
        return denied
    directory = directory_for(config)
    if not directory.available():
        return _json({"error": "directory tables are not available"}, 503)
    rows = directory.list_grants(
        resource_kind=request.query_params.get("resource_kind") or None,
        resource_id=request.query_params.get("resource_id") or None,
        scope_kind=request.query_params.get("scope_kind") or None,
        scope_id=request.query_params.get("scope_id") or None,
        active_only=False,
    )
    return _json({"grants": [_grant_payload(r) for r in rows]})


async def put_grant(request, config):
    denied = _require_admin(request, config)
    if denied is not None:
        return denied
    directory = directory_for(config)
    if not directory.available():
        return _json({"error": "directory tables are not available"}, 503)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    items = body.get("grants") if isinstance(body, dict) and isinstance(body.get("grants"), list) else None
    if items is None:
        items = [body] if isinstance(body, dict) else []
    saved = []
    for raw in items:
        if not isinstance(raw, dict):
            return _json({"error": "invalid grant"}, 400)
        rec = GrantRecord(
            grant_id=str(raw.get("grant_id") or ""),
            scope_kind=str(raw.get("scope_kind") or "").strip(),
            scope_id=str(raw.get("scope_id") or "").strip(),
            resource_kind=str(raw.get("resource_kind") or "").strip(),
            resource_id=str(raw.get("resource_id") or "").strip(),
            grant_effect=str(raw.get("grant_effect") or settings.grant_allow(config)).strip(),
            row_status=str(raw.get("row_status") or settings.acl_active(config)).strip(),
        )
        if rec.scope_kind not in settings.csv_field(settings.acl_cfg(config).scope_kinds):
            return _json({"error": f"invalid scope_kind: {rec.scope_kind}"}, 400)
        if rec.resource_kind not in settings.csv_field(settings.acl_cfg(config).resource_kinds):
            return _json({"error": f"invalid resource_kind: {rec.resource_kind}"}, 400)
        if rec.grant_effect not in (settings.grant_allow(config), settings.grant_deny(config)):
            return _json({"error": f"invalid grant_effect: {rec.grant_effect}"}, 400)
        try:
            saved.append(directory.upsert_grant(rec))
        except Exception as exc:
            return _json({"error": str(exc)}, 400)
    return _json({"grants": [_grant_payload(r) for r in saved]})


def identity_payload(config, user_id: str) -> Dict[str, Optional[str]]:
    role_id, org_id = resolve_identity(config, user_id)
    return {"user_id": user_id, "role_id": role_id, "org_id": org_id}
