"""Write / forget / search facade used by tools and HTTP."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Optional, Sequence

from ..privacy import contains_raw_pii, desensitize_text
from . import settings
from .embed import embedder_for
from .models import MemoryItem
from .resolve import identity_scopes, search_memories
from .store import MemoryStore, cosine, memory_store_for, utc_now

_ITEM_KEY_PAIR = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_ITEM_KEY_INSTANCE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9]+$")


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


def _require_item_key(config, item_key: str, *, allow_instance: bool) -> str:
    raw = (item_key or "").strip()
    if _ITEM_KEY_PAIR.fullmatch(raw):
        catalog = raw
    elif allow_instance and _ITEM_KEY_INSTANCE.fullmatch(raw):
        catalog = settings.catalog_item_key(raw)
    else:
        raise ValueError(
            "item_key must be domain.aspect using lowercase letters, digits, underscore"
        )
    domain = catalog.split(".", 1)[0]
    domains = settings.item_key_domains(config)
    if domains and domain not in domains:
        raise ValueError("item_key domain must be one of: " + ", ".join(domains))
    allowed = settings.item_keys(config)
    if allowed and catalog not in allowed:
        raise ValueError("item_key must be one of: " + ", ".join(allowed))
    return raw


def _catalog_candidates(
    items: Sequence[MemoryItem],
    *,
    catalog: str,
    scenario_code: str,
    mem_kind: str,
) -> list:
    out = []
    for item in items:
        if item.scenario_code != scenario_code or item.mem_kind != mem_kind:
            continue
        if not settings.item_key_matches_catalog(item.item_key, catalog):
            continue
        out.append(item)
    return out


def _best_merge_hit(items: Sequence[MemoryItem], query_vec, threshold: float):
    best = None
    best_score = -1.0
    for item in items:
        if not item.embedding:
            continue
        score = cosine(query_vec, item.embedding)
        if score > best_score:
            best_score = score
            best = item
    if best is None or best_score < threshold:
        return None
    return best


def _mint_instance_key(
    store: MemoryStore,
    *,
    scope_kind: str,
    scope_id: str,
    scenario_code: str,
    mem_kind: str,
    catalog: str,
) -> str:
    for _ in range(8):
        key = f"{catalog}.{secrets.token_hex(4)}"
        if store.get_by_key(scope_kind, scope_id, scenario_code, mem_kind, key) is None:
            return key
    return f"{catalog}.{secrets.token_hex(8)}"


def _resolve_instance_key(config, store: MemoryStore, item: MemoryItem) -> str:
    catalog = settings.catalog_item_key(item.item_key)
    threshold = settings.merge_score(config)
    query_vec = item.embedding or []
    same = _catalog_candidates(
        store.list_scope([(item.scope_kind, item.scope_id)], include_inactive=False),
        catalog=catalog,
        scenario_code=item.scenario_code,
        mem_kind=item.mem_kind,
    )
    hit = _best_merge_hit(same, query_vec, threshold)
    if hit is not None:
        return hit.item_key
    if (
        item.scope_kind == "user"
        and settings.merge_across_scopes(config)
        and query_vec
    ):
        parent_scopes = [
            pair
            for pair in identity_scopes(config, item.scope_id)
            if pair != (item.scope_kind, item.scope_id)
        ]
        if parent_scopes:
            parents = _catalog_candidates(
                store.list_scope(parent_scopes, include_inactive=False),
                catalog=catalog,
                scenario_code=item.scenario_code,
                mem_kind=item.mem_kind,
            )
            parent_hit = _best_merge_hit(parents, query_vec, threshold)
            if parent_hit is not None:
                return parent_hit.item_key
    return _mint_instance_key(
        store,
        scope_kind=item.scope_kind,
        scope_id=item.scope_id,
        scenario_code=item.scenario_code,
        mem_kind=item.mem_kind,
        catalog=catalog,
    )


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
    lock_item_key: bool = False,
) -> MemoryItem:
    store, embedder = ensure_ready(config)
    scope_kind = _require_enum(scope_kind, settings.scope_kinds(config), "scope_kind")
    scenario_code = _require_enum(scenario_code, settings.scenarios(config), "scenario_code")
    mem_kind = _require_enum(mem_kind, settings.kinds(config), "mem_kind")
    item_key = _require_item_key(config, item_key, allow_instance=lock_item_key)
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
        kb_status=settings.kb_status_none(config),
    )
    item.embedding = embedder.embed(item.embed_text())
    if not lock_item_key:
        item.item_key = _resolve_instance_key(config, store, item)
    existing = store.get_by_key(
        scope_kind, scope_id, scenario_code, mem_kind, item.item_key
    )
    if existing is not None:
        _apply_kb_on_content_write(config, item, existing)
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


def _apply_kb_on_content_write(config, item: MemoryItem, existing: MemoryItem) -> None:
    item.kb_status = settings.effective_kb_status(existing, config)
    item.kb_ref = existing.kb_ref
    item.kb_ingested_at = existing.kb_ingested_at
    item.kb_ingested_by = existing.kb_ingested_by
    content_changed = (
        (item.title_text or "") != (existing.title_text or "")
        or (item.body_text or "") != (existing.body_text or "")
        or (item.payload_text or "") != (existing.payload_text or "")
    )
    if content_changed and item.kb_status == settings.kb_status_ingested(config):
        item.kb_status = settings.kb_status_stale(config)


def filter_by_kb_status(config, items: Sequence[MemoryItem], kb_status: str) -> list:
    raw = (kb_status or "").strip()
    if not raw:
        return list(items)
    allowed = settings.kb_statuses(config)
    if raw not in allowed:
        raise ValueError("invalid kb_status: " + raw)
    return [item for item in items if settings.effective_kb_status(item, config) == raw]


def set_kb_harvest(
    config,
    item_id: str,
    *,
    actor: str,
    kb_status: Optional[str] = None,
    kb_ref=None,
    update_ref: bool = False,
    store: Optional[MemoryStore] = None,
) -> MemoryItem:
    store = store or memory_store_for(config)
    if store is None:
        raise MemoryUnavailable("long-term memory is not configured")
    item = store.get(item_id)
    if item is None:
        raise ValueError("memory not found")
    if kb_status is not None:
        item.kb_status = _require_enum(kb_status, settings.kb_statuses(config), "kb_status")
    else:
        item.kb_status = settings.effective_kb_status(item, config)
    if update_ref:
        if kb_ref is None:
            item.kb_ref = None
        else:
            ref = str(kb_ref).strip()
            if len(ref) > 512:
                raise ValueError("kb_ref is too long")
            item.kb_ref = ref or None
    if item.kb_status == settings.kb_status_ingested(config):
        now = utc_now()
        item.kb_ingested_at = now
        item.kb_ingested_by = actor
    return store.set_kb_harvest(item, actor=actor)


def write_user_memory(config, user_id: str, **kwargs) -> MemoryItem:
    kwargs.setdefault("scope_kind", "user")
    kwargs.setdefault("scope_id", user_id)
    kwargs.setdefault("origin_type", kwargs.get("origin_type") or "agent_inferred")
    return write_memory(config, actor=user_id, **kwargs)


def search_for_user(config, user_id: str, query: str):
    return search_memories(config, user_id, query)
