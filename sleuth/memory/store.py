"""OpenGauss memory store. Schema is hand-built; this module only reads and writes rows."""
from __future__ import annotations

import json
import logging
import math
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from ..util.ids import audit_id, memory_id
from . import settings
from .models import MemoryItem

log = logging.getLogger("sleuth.memory.store")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        xf = float(x)
        yf = float(y)
        dot += xf * yf
        na += xf * xf
        nb += yf * yf
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def format_vector(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in values) + "]"


def _is_active_unexpired(item: MemoryItem, config, now: datetime) -> bool:
    active = settings.row_status_active(config)
    if (item.row_status or "") != active:
        return False
    if item.expire_at is not None and item.expire_at <= now:
        return False
    return True


class MemoryStore:
    def available(self) -> bool:
        return True

    def get(self, item_id: str) -> Optional[MemoryItem]:
        raise NotImplementedError

    def get_by_key(
        self,
        scope_kind: str,
        scope_id: str,
        scenario_code: str,
        mem_kind: str,
        item_key: str,
    ) -> Optional[MemoryItem]:
        raise NotImplementedError

    def upsert(self, item: MemoryItem, *, actor: str, action_type: str) -> MemoryItem:
        raise NotImplementedError

    def archive(self, item_id: str, *, actor: str, action_type: str) -> Optional[MemoryItem]:
        raise NotImplementedError

    def search(
        self,
        query_vec: Sequence[float],
        scopes: Sequence[Tuple[str, str]],
        *,
        limit: int,
    ) -> List[MemoryItem]:
        raise NotImplementedError

    def list_scope(
        self,
        scopes: Sequence[Tuple[str, str]],
        *,
        mem_kinds: Optional[Sequence[str]] = None,
        include_inactive: bool = False,
    ) -> List[MemoryItem]:
        raise NotImplementedError

    def mark_used(self, item_ids: Sequence[str]) -> None:
        raise NotImplementedError


class InMemoryMemoryStore(MemoryStore):
    """Cosine search in process memory — used by tests."""

    def __init__(self, config):
        self.config = config
        self.items: dict[str, MemoryItem] = {}
        self.audits: list[dict] = []

    def available(self) -> bool:
        return True

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self.items.get(item_id)

    def get_by_key(
        self,
        scope_kind: str,
        scope_id: str,
        scenario_code: str,
        mem_kind: str,
        item_key: str,
    ) -> Optional[MemoryItem]:
        for item in self.items.values():
            if (
                item.scope_kind == scope_kind
                and item.scope_id == scope_id
                and item.scenario_code == scenario_code
                and item.mem_kind == mem_kind
                and item.item_key == item_key
            ):
                return item
        return None

    def upsert(self, item: MemoryItem, *, actor: str, action_type: str) -> MemoryItem:
        now = utc_now()
        existing = self.get_by_key(
            item.scope_kind, item.scope_id, item.scenario_code, item.mem_kind, item.item_key
        )
        if existing:
            item.id = existing.id
            item.created_at = existing.created_at
            item.created_by = existing.created_by
            item.use_cnt = existing.use_cnt
            item.last_used_at = existing.last_used_at
        else:
            item.id = item.id or memory_id()
            item.created_at = now
            item.created_by = actor
        item.updated_at = now
        item.updated_by = actor
        if not item.row_status:
            item.row_status = settings.row_status_active(self.config)
        self.items[item.id] = item
        self.audits.append(
            {
                "audit_id": audit_id(),
                "memory_id": item.id,
                "action_type": action_type,
                "actor_user_id": actor,
                "acted_at": now,
                "detail_text": f"item_key={item.item_key}",
            }
        )
        return item

    def archive(self, item_id: str, *, actor: str, action_type: str) -> Optional[MemoryItem]:
        item = self.items.get(item_id)
        if item is None:
            return None
        item.row_status = settings.row_status_archived(self.config)
        item.updated_at = utc_now()
        item.updated_by = actor
        self.audits.append(
            {
                "audit_id": audit_id(),
                "memory_id": item.id,
                "action_type": action_type,
                "actor_user_id": actor,
                "acted_at": item.updated_at,
                "detail_text": f"item_key={item.item_key}",
            }
        )
        return item

    def search(
        self,
        query_vec: Sequence[float],
        scopes: Sequence[Tuple[str, str]],
        *,
        limit: int,
    ) -> List[MemoryItem]:
        now = utc_now()
        scope_set = {(s, i) for s, i in scopes if s and i}
        scored: List[MemoryItem] = []
        for item in self.items.values():
            if (item.scope_kind, item.scope_id) not in scope_set:
                continue
            if not _is_active_unexpired(item, self.config, now):
                continue
            if not item.embedding:
                continue
            item.score = cosine(query_vec, item.embedding)
            scored.append(item)
        scored.sort(key=lambda x: x.score or 0.0, reverse=True)
        return scored[: max(0, int(limit))]

    def list_scope(
        self,
        scopes: Sequence[Tuple[str, str]],
        *,
        mem_kinds: Optional[Sequence[str]] = None,
        include_inactive: bool = False,
    ) -> List[MemoryItem]:
        now = utc_now()
        scope_set = {(s, i) for s, i in scopes if s and i}
        kinds = set(mem_kinds or [])
        out = []
        for item in self.items.values():
            if (item.scope_kind, item.scope_id) not in scope_set:
                continue
            if kinds and item.mem_kind not in kinds:
                continue
            if not include_inactive and not _is_active_unexpired(item, self.config, now):
                continue
            out.append(item)
        return out

    def mark_used(self, item_ids: Sequence[str]) -> None:
        now = utc_now()
        for iid in item_ids:
            item = self.items.get(iid)
            if item is None:
                continue
            item.last_used_at = now
            item.use_cnt = int(item.use_cnt or 0) + 1


def psycopg2_missing_message() -> str:
    exe = sys.executable or "python"
    return (
        f"psycopg2 is not importable in this process ({exe}). "
        f'Install it into the same interpreter: "{exe}" -m pip install psycopg2-binary. '
        "Do not pip install sleuth[memory] from a corporate PyPI index "
        "(this project is not published there). "
        f'From a local checkout: "{exe}" -m pip install -e ".[memory]".'
    )


class OpenGaussStore(MemoryStore):
    """Reads/writes hand-built OpenGauss tables. No schema migration."""

    def __init__(self, config):
        self.config = config
        self._ok: Optional[bool] = None
        self.last_error = ""

    def available(self) -> bool:
        if self._ok is not None:
            return self._ok
        try:
            with self._connect() as conn:
                self._ok = self._tables_exist(conn)
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("OpenGauss memory unavailable: %s", exc)
            self._ok = False
        if not self._ok:
            log.warning("memory disabled: %s", self.last_error or "tables missing or unreachable")
        return bool(self._ok)

    def _connect_kwargs(self):
        mem = settings.memory_cfg(self.config)
        timeout = int(settings.connect_timeout_s(self.config) or 5)
        if mem.og_dsn:
            return {"dsn": mem.og_dsn, "connect_timeout": timeout}
        return {
            "host": mem.og_host,
            "port": int(mem.og_port),
            "user": mem.og_user,
            "password": mem.og_password,
            "dbname": mem.og_database,
            "connect_timeout": timeout,
        }

    @contextmanager
    def _connect(self):
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError(psycopg2_missing_message()) from exc
        conn = psycopg2.connect(**self._connect_kwargs())
        schema = settings.og_schema(self.config)
        if schema:
            cur = conn.cursor()
            cur.execute(f"SET search_path TO {schema}")
            cur.close()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _tables_exist(self, conn) -> bool:
        """Probe by selecting from the real tables."""
        cur = conn.cursor()
        for ref in (
            settings.table_item_ref(self.config),
            settings.table_audit_ref(self.config),
        ):
            cur.execute(f"SELECT 1 FROM {ref} LIMIT 0")
        return True

    def get(self, item_id: str) -> Optional[MemoryItem]:
        if not self.available():
            return None
        table = settings.table_item_ref(self.config)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {_ITEM_COLS} FROM {table} WHERE id = %s", (item_id,))
            row = cur.fetchone()
        return _row_to_item(row) if row else None

    def get_by_key(
        self,
        scope_kind: str,
        scope_id: str,
        scenario_code: str,
        mem_kind: str,
        item_key: str,
    ) -> Optional[MemoryItem]:
        if not self.available():
            return None
        table = settings.table_item_ref(self.config)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT {_ITEM_COLS} FROM {table} "
                "WHERE scope_kind = %s AND scope_id = %s AND scenario_code = %s "
                "AND mem_kind = %s AND item_key = %s",
                (scope_kind, scope_id, scenario_code, mem_kind, item_key),
            )
            row = cur.fetchone()
        return _row_to_item(row) if row else None

    def upsert(self, item: MemoryItem, *, actor: str, action_type: str) -> MemoryItem:
        if not self.available():
            raise RuntimeError("memory store is unavailable")
        now = utc_now()
        existing = self.get_by_key(
            item.scope_kind, item.scope_id, item.scenario_code, item.mem_kind, item.item_key
        )
        table = settings.table_item_ref(self.config)
        audit_table = settings.table_audit_ref(self.config)
        if existing:
            item.id = existing.id
            item.created_at = existing.created_at
            item.created_by = existing.created_by
            item.use_cnt = existing.use_cnt
            item.last_used_at = existing.last_used_at
        else:
            item.id = item.id or memory_id()
            item.created_at = now
            item.created_by = actor
        item.updated_at = now
        item.updated_by = actor
        if not item.row_status:
            item.row_status = settings.row_status_active(self.config)
        vec = _bind_embedding(item.embedding, self.config)
        body = encode_text_field(item.body_text, self.config)
        payload = encode_text_field(item.payload_text, self.config)
        text_ph = _text_placeholder(self.config)
        emb_ph = _embedding_placeholder(self.config)
        with self._connect() as conn:
            cur = conn.cursor()
            if existing:
                cur.execute(
                    f"UPDATE {table} SET title_text = %s, body_text = {text_ph}, payload_text = {text_ph}, "
                    f"embedding = {emb_ph}, importance_score = %s, confidence_score = %s, "
                    "origin_type = %s, row_status = %s, expire_at = %s, updated_by = %s, "
                    "updated_at = %s WHERE id = %s",
                    (
                        item.title_text,
                        body,
                        payload,
                        vec,
                        int(item.importance_score),
                        item.confidence_score,
                        item.origin_type,
                        item.row_status,
                        item.expire_at,
                        item.updated_by,
                        item.updated_at,
                        item.id,
                    ),
                )
            else:
                cur.execute(
                    f"INSERT INTO {table} (id, scope_kind, scope_id, scenario_code, mem_kind, "
                    "item_key, title_text, body_text, payload_text, embedding, importance_score, "
                    "confidence_score, origin_type, row_status, expire_at, created_by, "
                    "updated_by, created_at, updated_at, last_used_at, use_cnt) "
                    f"VALUES (%s,%s,%s,%s,%s,%s,%s,{text_ph},{text_ph},{emb_ph},%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        item.id,
                        item.scope_kind,
                        item.scope_id,
                        item.scenario_code,
                        item.mem_kind,
                        item.item_key,
                        item.title_text,
                        body,
                        payload,
                        vec,
                        int(item.importance_score),
                        item.confidence_score,
                        item.origin_type,
                        item.row_status,
                        item.expire_at,
                        item.created_by,
                        item.updated_by,
                        item.created_at,
                        item.updated_at,
                        item.last_used_at,
                        int(item.use_cnt or 0),
                    ),
                )
            cur.execute(
                f"INSERT INTO {audit_table} (audit_id, memory_id, action_type, actor_user_id, "
                "acted_at, detail_text) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    audit_id(),
                    item.id,
                    action_type,
                    actor,
                    now,
                    f"item_key={item.item_key}",
                ),
            )
        return item

    def archive(self, item_id: str, *, actor: str, action_type: str) -> Optional[MemoryItem]:
        if not self.available():
            return None
        item = self.get(item_id)
        if item is None:
            return None
        now = utc_now()
        archived = settings.row_status_archived(self.config)
        table = settings.table_item_ref(self.config)
        audit_table = settings.table_audit_ref(self.config)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {table} SET row_status = %s, updated_by = %s, updated_at = %s WHERE id = %s",
                (archived, actor, now, item_id),
            )
            cur.execute(
                f"INSERT INTO {audit_table} (audit_id, memory_id, action_type, actor_user_id, "
                "acted_at, detail_text) VALUES (%s,%s,%s,%s,%s,%s)",
                (audit_id(), item_id, action_type, actor, now, f"item_key={item.item_key}"),
            )
        item.row_status = archived
        item.updated_at = now
        item.updated_by = actor
        return item

    def search(
        self,
        query_vec: Sequence[float],
        scopes: Sequence[Tuple[str, str]],
        *,
        limit: int,
    ) -> List[MemoryItem]:
        if not self.available() or not scopes:
            return []
        if not settings.uses_sql_ann(self.config):
            return self._search_real_array(query_vec, scopes, limit=limit)
        table = settings.table_item_ref(self.config)
        active = settings.row_status_active(self.config)
        clauses = []
        params: list = []
        for scope_kind, scope_id in scopes:
            if not scope_kind or not scope_id:
                continue
            clauses.append("(scope_kind = %s AND scope_id = %s)")
            params.extend([scope_kind, scope_id])
        if not clauses:
            return []
        vec = format_vector(query_vec)
        vec_cast = settings.vector_sql_type(self.config)
        sql = (
            f"SELECT {_ITEM_COLS}, (1 - (embedding <=> %s::{vec_cast})) AS score "
            f"FROM {table} WHERE row_status = %s "
            f"AND (expire_at IS NULL OR expire_at > NOW()) "
            f"AND ({' OR '.join(clauses)}) "
            f"AND embedding IS NOT NULL "
            f"ORDER BY embedding <=> %s::{vec_cast} LIMIT %s"
        )
        bind = [vec, active, *params, vec, int(limit)]
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, bind)
                rows = cur.fetchall()
        except Exception as exc:
            log.warning("SQL ANN search failed (%s); falling back to in-process cosine", exc)
            return self._search_real_array(query_vec, scopes, limit=limit)
        items = []
        for row in rows:
            item = _row_to_item(row[:-1])
            if item is None:
                continue
            item.score = float(row[-1] or 0.0)
            items.append(item)
        return items

    def _search_real_array(
        self,
        query_vec: Sequence[float],
        scopes: Sequence[Tuple[str, str]],
        *,
        limit: int,
    ) -> List[MemoryItem]:
        items = self.list_scope(scopes, include_inactive=False)
        scored = []
        for item in items:
            if not item.embedding:
                continue
            item.score = cosine(query_vec, item.embedding)
            scored.append(item)
        scored.sort(key=lambda x: x.score or 0.0, reverse=True)
        return scored[: max(0, int(limit))]

    def list_scope(
        self,
        scopes: Sequence[Tuple[str, str]],
        *,
        mem_kinds: Optional[Sequence[str]] = None,
        include_inactive: bool = False,
    ) -> List[MemoryItem]:
        if not self.available() or not scopes:
            return []
        table = settings.table_item_ref(self.config)
        clauses = []
        params: list = []
        for scope_kind, scope_id in scopes:
            if not scope_kind or not scope_id:
                continue
            clauses.append("(scope_kind = %s AND scope_id = %s)")
            params.extend([scope_kind, scope_id])
        if not clauses:
            return []
        sql = f"SELECT {_ITEM_COLS} FROM {table} WHERE ({' OR '.join(clauses)})"
        if not include_inactive:
            sql += " AND row_status = %s AND (expire_at IS NULL OR expire_at > NOW())"
            params.append(settings.row_status_active(self.config))
        if mem_kinds:
            sql += " AND mem_kind = ANY(%s)"
            params.append(list(mem_kinds))
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [item for item in (_row_to_item(r) for r in rows) if item]

    def mark_used(self, item_ids: Sequence[str]) -> None:
        ids = [i for i in item_ids if i]
        if not ids or not self.available():
            return
        table = settings.table_item_ref(self.config)
        now = utc_now()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {table} SET last_used_at = %s, use_cnt = use_cnt + 1 WHERE id = ANY(%s)",
                (now, ids),
            )


_ITEM_COLS = (
    "id, scope_kind, scope_id, scenario_code, mem_kind, item_key, title_text, "
    "body_text, payload_text, embedding, importance_score, confidence_score, "
    "origin_type, row_status, expire_at, created_by, updated_by, created_at, "
    "updated_at, last_used_at, use_cnt"
)


def _parse_embedding(raw) -> Optional[List[float]]:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    elif text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text:
        return []
    return [float(p) for p in text.split(",") if p.strip()]


def _bind_embedding(values: Optional[Sequence[float]], config):
    if values is None:
        return None
    if settings.uses_sql_ann(config):
        return format_vector(values)
    return list(float(x) for x in values)


def _text_placeholder(config) -> str:
    if settings.text_kind(config) == "jsonb":
        return "%s::jsonb"
    return "%s"


def _embedding_placeholder(config) -> str:
    vec_cast = settings.vector_sql_type(config)
    if vec_cast:
        return f"%s::{vec_cast}"
    return "%s"


def encode_text_field(value, config):
    if settings.text_kind(config) != "jsonb":
        return value
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            pass
    return json.dumps(text, ensure_ascii=False)


def decode_text_field(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes().decode("utf-8")
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _row_to_item(row) -> Optional[MemoryItem]:
    if not row:
        return None
    return MemoryItem(
        id=row[0],
        scope_kind=row[1],
        scope_id=row[2],
        scenario_code=row[3],
        mem_kind=row[4],
        item_key=row[5],
        title_text="" if row[6] is None else str(row[6]),
        body_text=decode_text_field(row[7]) or "",
        payload_text=decode_text_field(row[8]),
        embedding=_parse_embedding(row[9]),
        importance_score=int(row[10] or 0),
        confidence_score=str(row[11] if row[11] is not None else "0"),
        origin_type=row[12],
        row_status=row[13],
        expire_at=row[14],
        created_by=row[15],
        updated_by=row[16],
        created_at=row[17],
        updated_at=row[18],
        last_used_at=row[19],
        use_cnt=int(row[20] or 0),
    )


_resolved_stores = {}


def memory_store_for(config) -> Optional[MemoryStore]:
    injected = getattr(config, "_memory_store", None)
    if injected is not None:
        return injected
    if not settings.memory_backend_on(config):
        setattr(config, "_memory_error", "SLEUTH_MEMORY_BACKEND is off")
        return None
    mem = settings.memory_cfg(config)
    key = (mem.og_dsn, mem.og_host, mem.og_database, mem.og_schema, mem.table_item, mem.table_audit)
    if key in _resolved_stores:
        return _resolved_stores[key]
    store = OpenGaussStore(config)
    if store.available():
        setattr(config, "_memory_error", "")
        _resolved_stores[key] = store
        return store
    setattr(config, "_memory_error", store.last_error or "memory tables missing or unreachable")
    _resolved_stores[key] = None
    return None
