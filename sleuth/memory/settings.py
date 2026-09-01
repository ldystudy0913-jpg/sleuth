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


def merge_score(config) -> float:
    raw = getattr(memory_cfg(config), "merge_score", None)
    try:
        return float(Decimal(str(raw)))
    except (InvalidOperation, TypeError, ValueError):
        return float(Decimal(str(memory_cfg(config).__dataclass_fields__["merge_score"].default)))


def merge_across_scopes(config) -> bool:
    return bool(getattr(memory_cfg(config), "merge_across_scopes", True))


def catalog_item_key(item_key: str) -> str:
    parts = [p for p in (item_key or "").strip().split(".") if p]
    if len(parts) >= 2:
        return parts[0] + "." + parts[1]
    return (item_key or "").strip()


def item_key_matches_catalog(item_key: str, catalog: str) -> bool:
    raw = (item_key or "").strip()
    cat = (catalog or "").strip()
    if not raw or not cat:
        return False
    return raw == cat or raw.startswith(cat + ".")


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


def item_key_domains(config) -> List[str]:
    return csv_field(getattr(memory_cfg(config), "item_key_domains", None))


def item_keys(config) -> List[str]:
    return csv_field(getattr(memory_cfg(config), "item_keys", None))


# DB keys stay English; labels help the model pick the catalog key.
ITEM_KEY_LABELS = {
    "output.language": "回复语言",
    "output.structure": "回复结构",
    "output.tone": "回复语气",
    "workflow.steps": "个人作业步骤",
    "str.threshold": "口径/定义/门槛（夜间交易时间窗口、金额阈值等）",
    "str.steps": "可疑步骤",
    "str.narrative": "可疑叙述结构",
    "dd.steps": "尽调步骤",
    "dd.sources": "尽调材料",
    "screening.steps": "筛查步骤",
    "screening.hits": "命中处置",
    "rating.factors": "评级因子",
    "rating.scale": "评级尺度",
    "policy.branch": "分行制度",
    "policy.head": "总行制度",
    "avoid.verbose_english": "避免英文长段",
    "avoid.raw_id": "避免原文证件号",
    "pattern.cash_night": "夜间现金分析套路（不是时间窗口口径）",
    "pattern.mule": "分散对手/骡子套路",
    "customer.segment": "客群",
    "customer.risk_note": "风险备注",
    "customer.id": "客户标识口径（已脱敏）",
    "usage.tables": "常用表（库表名、schema、常查的业务表）",
    "usage.fields": "常用字段（列名、码值、常筛维度）",
    "usage.habit": "其他用数习惯（默认筛选、常用关联、取数路径等）",
}


def item_key_write_guide() -> str:
    lines = [
        "Catalog key domain.aspect. Choose by meaning, not the first example: "
        "口径/定义/时间窗口/门槛 → str.threshold; "
        "回复语言/语气/结构 → output.language|structure|tone; "
        "常用表 → usage.tables; 常用字段 → usage.fields; 其他用数习惯 → usage.habit; "
        "夜间现金分析套路 → pattern.cash_night. "
        "Pass the catalog key only; do not invent suffixes.",
    ]
    lines.extend(f"{key}: {label}" for key, label in ITEM_KEY_LABELS.items())
    return "\n".join(lines)


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


def ann_distance_sql(config) -> tuple:
    """SQL snippets: (score_expr, order_expr). Each contains one ``%s`` for the query vector.

    pgvector ``vector`` uses ``<=>`` (cosine distance). GaussDB/OpenGauss
    ``floatvector`` has no ``<=>``; cosine distance is ``<+>`` / ``cosine_distance``.
    Similarity score is ``1 - distance`` in both cases.
    """
    cast = vector_sql_type(config)
    if vector_kind(config) == "floatvector":
        dist = f"cosine_distance(embedding, %s::{cast})"
    else:
        dist = f"(embedding <=> %s::{cast})"
    return f"(1 - {dist})", dist


def text_kind(config) -> str:
    return (getattr(memory_cfg(config), "text_kind", None) or "").strip().lower()


def row_status_active(config) -> str:
    return (memory_cfg(config).row_status_active or "").strip()


def row_status_archived(config) -> str:
    return (memory_cfg(config).row_status_archived or "").strip()


def kb_status_none(config) -> str:
    return (memory_cfg(config).kb_status_none or "").strip() or "none"


def kb_status_nominated(config) -> str:
    return (memory_cfg(config).kb_status_nominated or "").strip() or "nominated"


def kb_status_ingested(config) -> str:
    return (memory_cfg(config).kb_status_ingested or "").strip() or "ingested"


def kb_status_stale(config) -> str:
    return (memory_cfg(config).kb_status_stale or "").strip() or "stale"


def kb_statuses(config) -> List[str]:
    return [
        kb_status_none(config),
        kb_status_nominated(config),
        kb_status_ingested(config),
        kb_status_stale(config),
    ]


def effective_kb_status(item, config) -> str:
    raw = (getattr(item, "kb_status", None) or "").strip()
    return raw or kb_status_none(config)


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


def grant_wildcard_id(config) -> str:
    return (acl_cfg(config).wildcard_id or "").strip()


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
