"""Write / forget / search facade used by tools and HTTP."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from ..privacy import contains_raw_pii, desensitize_text
from . import settings
from .embed import embedder_for
from .models import MemoryItem
from .resolve import identity_scopes, search_memories
from .store import MemoryStore, memory_store_for, utc_now

_ITEM_KEY = re.compile(r"^[a-z0-9._]+$")


class MemoryPrivacyError(ValueError):
    pass


class MemoryUnavailable(RuntimeError):
    pass


def ensure_ready(config) -> tuple:
    store = memory_store_for(config)
    embedder = embedder_for(config)
    if store is None or embedder is None:
        raise MemoryUnavailable("long-term memory is not configured")
    return store, embedder


def _require_enum(value: str, allowed, label: str) -> str:
    raw = (value or "").strip()
    if raw not in allowed:
        raise ValueError(f"invalid {label}: {value!r}")
    return raw


def prepare_text(title: str, body: str) -> tuple:
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise ValueError("title_text and body_text are required")
    if contains_raw_pii(title) or contains_raw_pii(body):
        raise MemoryPrivacyError("refusing to store raw identity numbers")
    title = desensitize_text(title)
    body = desensitize_text(body)
    if contains_raw_pii(title) or contains_raw_pii(body):
        raise MemoryPrivacyError("refusing to store raw identity numbers")
    return title, body


def write_memory(
    config,
    *,
    actor: str,
    scope_kind: str,
    scope_id: str,
    scenario_code: str,
    mem_kind: str,
    item_key: str,
    title_text: str,
    body_text: str,
    payload_text: Optional[str] = None,
    importance_score: int = 3,
    confidence_score: str = "1.0000",
    origin_type: str = "",
    expire_at=None,
) -> MemoryItem:
    store, embedder = ensure_ready(config)
    scope_kind = _require_enum(scope_kind, settings.scope_kinds(config), "scope_kind")
    scenario_code = _require_enum(scenario_code, settings.scenarios(config), "scenario_code")
    mem_kind = _require_enum(mem_kind, settings.kinds(config), "mem_kind")
    item_key = (item_key or "").strip()
    if not _ITEM_KEY.fullmatch(item_key):
        raise ValueError("item_key must be lowercase letters, digits, dots, or underscores")
    title_text, body_text = prepare_text(title_text, body_text)
    origins = settings.origins(config)
    origin_type = (origin_type or "").strip() or (origins[0] if origins else "")
    if origins and origin_type not in origins:
        raise ValueError(f"invalid origin_type: {origin_type!r}")
    try:
        importance_score = int(importance_score)
    except (TypeError, ValueError) as exc:
        raise ValueError("importance_score must be an integer") from exc
    if importance_score < 1 or importance_score > 5:
        raise ValueError("importance_score must be 1-5")
    if expire_at is None and mem_kind in settings.ttl_kinds(config):
        days = settings.pattern_ttl_days(config)
        if days > 0:
            expire_at = utc_now() + timedelta(days=days)
    item = MemoryItem(
        id="",
        scope_kind=scope_kind,
        scope_id=scope_id,
        scenario_code=scenario_code,
        mem_kind=mem_kind,
        item_key=item_key,
        title_text=title_text,
        body_text=body_text,
        payload_text=payload_text,
        importance_score=importance_score,
        confidence_score=str(confidence_score),
        origin_type=origin_type,
        row_status=settings.row_status_active(config),
        expire_at=expire_at,
    )
    item.embedding = embedder.embed(item.embed_text())
    existing = store.get_by_key(scope_kind, scope_id, scenario_code, mem_kind, item_key)
    action = "update" if existing else "create"
    return store.upsert(item, actor=actor, action_type=action)


def forget_memory(config, item_id: str, *, actor: str, store: Optional[MemoryStore] = None):
    store = store or memory_store_for(config)
    if store is None:
        raise MemoryUnavailable("long-term memory is not configured")
    item = store.archive(item_id, actor=actor, action_type="forget")
    if item is None:
        raise ValueError("memory not found")
    return item


def can_access_item(config, user_id: str, item: MemoryItem) -> bool:
    scopes = {(s, i) for s, i in identity_scopes(config, user_id)}
    return (item.scope_kind, item.scope_id) in scopes


def write_user_memory(config, user_id: str, **kwargs) -> MemoryItem:
    kwargs.setdefault("scope_kind", "user")
    kwargs.setdefault("scope_id", user_id)
    kwargs.setdefault("origin_type", kwargs.get("origin_type") or "agent_inferred")
    return write_memory(config, actor=user_id, **kwargs)


def search_for_user(config, user_id: str, query: str):
    return search_memories(config, user_id, query)
