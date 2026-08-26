"""Read memory / ACL knobs from Config. No business-code literals for tunables."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import List, Optional

from .sqlident import sql_ident


def csv_field(raw: Optional[str]) -> List[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def memory_cfg(config):
    return getattr(config, "memory", None)


def acl_cfg(config):
    return getattr(config, "acl", None)


def memory_backend_on(config) -> bool:
    mem = memory_cfg(config)
    if mem is None:
        return False
    return (mem.backend or "").strip().lower() not in ("", "off", "0", "false", "none")


def table_item(config) -> str:
    return sql_ident(memory_cfg(config).table_item)


def table_audit(config) -> str:
    return sql_ident(memory_cfg(config).table_audit)


def og_schema(config) -> str:
    raw = (getattr(memory_cfg(config), "og_schema", None) or "").strip()
    if not raw:
        return ""
    return sql_ident(raw)


def _qualify(config, table_name: str) -> str:
    schema = og_schema(config)
    if schema:
        return f"{schema}.{table_name}"
    return table_name


def table_item_ref(config) -> str:
    return _qualify(config, table_item(config))


def table_audit_ref(config) -> str:
    return _qualify(config, table_audit(config))


def table_org(config) -> str:
    return sql_ident(acl_cfg(config).table_org)


def table_role(config) -> str:
    return sql_ident(acl_cfg(config).table_role)


def table_user(config) -> str:
    return sql_ident(acl_cfg(config).table_user)


def table_grant(config) -> str:
    return sql_ident(acl_cfg(config).table_grant)


def embedding_dim(config) -> int:
    return int(memory_cfg(config).embedding_dim)


def top_k(config) -> int:
    return int(memory_cfg(config).top_k)


def min_score(config) -> float:
    raw = memory_cfg(config).min_score
    try:
        return float(Decimal(str(raw)))
    except (InvalidOperation, TypeError, ValueError):
        return float(Decimal(str(memory_cfg(config).__dataclass_fields__["min_score"].default)))


def max_items(config) -> int:
    return int(memory_cfg(config).max_items)


def max_chars(config) -> int:
    return int(memory_cfg(config).max_chars)


def pattern_ttl_days(config) -> int:
    return int(memory_cfg(config).pattern_ttl_days)


def scenarios(config) -> List[str]:
    return csv_field(memory_cfg(config).scenarios)


def kinds(config) -> List[str]:
    return csv_field(memory_cfg(config).kinds)


def pin_kinds(config) -> List[str]:
    return csv_field(memory_cfg(config).pin_kinds)


def ttl_kinds(config) -> List[str]:
    return csv_field(memory_cfg(config).ttl_kinds)


def scope_kinds(config) -> List[str]:
    return csv_field(memory_cfg(config).scope_kinds)


def origins(config) -> List[str]:
    return csv_field(memory_cfg(config).origins)


def vector_kind(config) -> str:
    return (memory_cfg(config).vector_kind or "").strip().lower()


def uses_sql_ann(config) -> bool:
    return vector_kind(config) in ("vector", "floatvector")


def vector_sql_type(config) -> str:
    kind = vector_kind(config)
    if kind in ("vector", "floatvector"):
        return sql_ident(kind)
    return ""


def text_kind(config) -> str:
    return (getattr(memory_cfg(config), "text_kind", None) or "").strip().lower()


def row_status_active(config) -> str:
    return (memory_cfg(config).row_status_active or "").strip()


def row_status_archived(config) -> str:
    return (memory_cfg(config).row_status_archived or "").strip()


def acl_enabled(config) -> bool:
    acl = acl_cfg(config)
    return bool(acl and acl.enabled)


def acl_active(config) -> str:
    return (acl_cfg(config).row_status_active or "").strip()


def acl_disabled(config) -> str:
    return (acl_cfg(config).row_status_disabled or "").strip()


def grant_allow(config) -> str:
    return (acl_cfg(config).grant_allow or "").strip()


def grant_deny(config) -> str:
    return (acl_cfg(config).grant_deny or "").strip()


def default_agent_name(config) -> str:
    acl = acl_cfg(config)
    named = (getattr(acl, "default_agent_name", None) or "").strip() if acl else ""
    if named:
        return named
    return (getattr(config, "default_agent", None) or "").strip()


def default_agent_open(config) -> bool:
    acl = acl_cfg(config)
    return bool(acl and acl.default_agent_open)


def connect_timeout_s(config) -> float:
    return float(memory_cfg(config).og_connect_timeout_s)
