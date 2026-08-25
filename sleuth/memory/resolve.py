"""Vector recall + pin kinds + user > role > org key override."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import settings
from .acl import resolve_identity
from .embed import embedder_for
from .models import MemoryItem
from .store import memory_store_for


def identity_scopes(config, user_id: str) -> List[Tuple[str, str]]:
    scopes: List[Tuple[str, str]] = []
    kinds = settings.scope_kinds(config)
    if user_id and "user" in kinds:
        scopes.append(("user", user_id))
    role_id, org_id = resolve_identity(config, user_id)
    if role_id and "role" in kinds:
        scopes.append(("role", role_id))
    if org_id and "org" in kinds:
        scopes.append(("org", org_id))
    return scopes


def _scope_rank(scope_kind: str, config) -> int:
    order = settings.scope_kinds(config)
    try:
        return len(order) - order.index(scope_kind)
    except ValueError:
        return 0


def _override_by_key(items: Sequence[MemoryItem], config) -> List[MemoryItem]:
    best: dict[str, MemoryItem] = {}
    for item in items:
        prev = best.get(item.item_key)
        if prev is None or _scope_rank(item.scope_kind, config) > _scope_rank(
            prev.scope_kind, config
        ):
            best[item.item_key] = item
    return list(best.values())


def retrieve_for_prompt(
    config,
    user_id: str,
    query: str,
    *,
    extra_scopes: Optional[Sequence[Tuple[str, str]]] = None,
) -> List[MemoryItem]:
    store = memory_store_for(config)
    embedder = embedder_for(config)
    if store is None or embedder is None:
        return []
    scopes = list(identity_scopes(config, user_id))
    if extra_scopes:
        scopes.extend(extra_scopes)
    if not scopes:
        return []
    query_vec = embedder.embed(query or "")
    hits = store.search(query_vec, scopes, limit=settings.top_k(config))
    threshold = settings.min_score(config)
    scored = [h for h in hits if (h.score or 0.0) >= threshold]
    pins = store.list_scope(
        [s for s in scopes if s[0] == "user"],
        mem_kinds=settings.pin_kinds(config),
        include_inactive=False,
    )
    seen = {item.id for item in scored}
    merged = list(scored)
    for pin in pins:
        if pin.id not in seen:
            merged.append(pin)
            seen.add(pin.id)
    merged = _override_by_key(merged, config)
    merged.sort(
        key=lambda it: (
            1 if it.mem_kind in settings.pin_kinds(config) and it.scope_kind == "user" else 0,
            it.score or 0.0,
            int(it.importance_score or 0),
        ),
        reverse=True,
    )
    limit = settings.max_items(config)
    chosen = merged[: max(0, limit)]
    budget = settings.max_chars(config)
    out: List[MemoryItem] = []
    used = 0
    for item in chosen:
        chunk = (item.title_text or "") + (item.body_text or "")
        if budget > 0 and used + len(chunk) > budget and out:
            break
        out.append(item)
        used += len(chunk)
    if out:
        try:
            store.mark_used([item.id for item in out])
        except Exception:
            pass
    return out


def search_memories(config, user_id: str, query: str) -> List[MemoryItem]:
    store = memory_store_for(config)
    embedder = embedder_for(config)
    if store is None or embedder is None:
        return []
    scopes = identity_scopes(config, user_id)
    if not scopes:
        return []
    hits = store.search(embedder.embed(query or ""), scopes, limit=settings.top_k(config))
    threshold = settings.min_score(config)
    return _override_by_key([h for h in hits if (h.score or 0.0) >= threshold], config)
