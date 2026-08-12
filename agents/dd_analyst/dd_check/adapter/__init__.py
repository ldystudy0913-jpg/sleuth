"""表单适配：把业务字段 result（JSON 字符串）解析成 ReportFacts。

节点 parse_report 调用 ReportAdapter().parse(req.result)。

字段查找语义（resolve_field）：
- ABSENT：报告未出现该字段（本场景不涉及）
- EMPTY：出现但值为空/仅空白（漏填）
- VALUE：出现且有非空值
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Dict, List, Tuple

from ..models import ReportFacts, ReportSection

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class FieldStatus(str, Enum):
    ABSENT = "absent"
    EMPTY = "empty"
    VALUE = "value"


def strip_html(text: str) -> str:
    """去掉 HTML 标签并压缩空白。"""
    t = _HTML_TAG.sub("", text or "")
    return _WS.sub(" ", t).strip()


def _norm_key(key: str) -> str:
    return (key or "").strip()


def _flatten_value_item(item: Any) -> Dict[str, str]:
    """表格/复选一行 value 展平成 str 字典。"""
    if not isinstance(item, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in item.items():
        nk = _norm_key(str(k))
        if not nk:
            continue
        if isinstance(v, (dict, list)):
            out[nk] = json.dumps(v, ensure_ascii=False)
        else:
            out[nk] = strip_html(str(v) if v is not None else "")
    return out


class ReportAdapter:
    """银行表单 payload 适配：section 列表 {label, code, type, value}。"""

    TABLE_TYPES = {
        "table-display",
        "tabled-input",
        "dynamic-edit-table",
        "simple-info-display",
        "item-info-display",
    }
    CHECKBOX_TYPES = {
        "explained-check-box",
        "additional-check-box",
    }
    NARRATIVE_TYPES = {
        "multi-line-input",
    }

    def parse(self, result: str) -> ReportFacts:
        """入口：JSON → sections → fields/tables/checkboxes。"""
        sections_raw = self._load_sections(result)
        facts = ReportFacts(raw_section_count=len(sections_raw))
        for raw in sections_raw:
            if not isinstance(raw, dict):
                continue
            section = ReportSection(
                label=_norm_key(str(raw.get("label") or "")),
                code=_norm_key(str(raw.get("code") or "")),
                type=str(raw.get("type") or "").strip(),
                value=raw.get("value"),
            )
            facts.sections.append(section)
            self._ingest_section(facts, section)
        return facts

    def _load_sections(self, result: str) -> List[Any]:
        """兼容数组 / {sections:[...]} / 单对象。"""
        text = (result or "").strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("sections"), list):
            return data["sections"]
        if isinstance(data, dict):
            return [data]
        raise ValueError("result must be a JSON array of form sections")

    def _ingest_section(self, facts: ReportFacts, section: ReportSection) -> None:
        """按 type 分流写入 fields / tables / checkboxes。"""
        code = section.code
        stype = section.type
        value = section.value

        if stype in self.CHECKBOX_TYPES:
            rows: List[Dict[str, str]] = []
            if isinstance(value, list):
                for item in value:
                    rows.append(_flatten_value_item(item))
            facts.checkboxes[code] = rows
            return

        if stype in self.NARRATIVE_TYPES:
            facts.narrative_codes.append(code)
            if isinstance(value, list):
                for item in value:
                    flat = _flatten_value_item(item)
                    for k, v in flat.items():
                        facts.fields[f"{code}.{k}"] = v
                        # keep first non-empty as section-level value; empty still declares key
                        if code not in facts.fields:
                            facts.fields[code] = v
                        elif not facts.fields[code].strip() and v.strip():
                            facts.fields[code] = v
            elif value is None:
                facts.fields[code] = ""
            else:
                facts.fields[code] = strip_html(str(value))
            return

        if stype in self.TABLE_TYPES or isinstance(value, list):
            rows = []
            if isinstance(value, list):
                for item in value:
                    flat = _flatten_value_item(item)
                    if flat:
                        rows.append(flat)
                        if len(flat) == 1:
                            k, v = next(iter(flat.items()))
                            facts.fields[f"{code}.{k}"] = v
                            facts.fields[k] = v
            facts.tables[code] = rows
            return

        if isinstance(value, str):
            facts.fields[code] = strip_html(value)
        elif value is not None:
            facts.fields[code] = strip_html(str(value))
        else:
            facts.fields[code] = ""


def _lookup_key(facts: ReportFacts, *candidates: str) -> str:
    """稳定查找：精确 → strip → 大小写折叠。不做贪婪子串匹配。"""
    if not candidates:
        return ""
    keys = list(facts.fields.keys())
    # exact
    for cand in candidates:
        c = _norm_key(cand)
        if not c:
            continue
        if c in facts.fields:
            return c
    # case-insensitive on stripped keys
    lower_map = {_norm_key(k).lower(): k for k in keys}
    for cand in candidates:
        c = _norm_key(cand)
        if not c:
            continue
        hit = lower_map.get(c.lower())
        if hit is not None:
            return hit
    return ""


def resolve_field(facts: ReportFacts, *candidates: str) -> Tuple[FieldStatus, str, str]:
    """按候选名解析字段存在性与值。

    Returns:
        (status, matched_key, value) — ABSENT 时 key/value 为空串。
    """
    key = _lookup_key(facts, *candidates)
    if not key:
        return FieldStatus.ABSENT, "", ""
    raw = facts.fields.get(key, "")
    value = raw if isinstance(raw, str) else str(raw or "")
    if not value.strip():
        return FieldStatus.EMPTY, key, value
    return FieldStatus.VALUE, key, value


def find_field(facts: ReportFacts, *candidates: str) -> Tuple[str, str]:
    """兼容旧接口：仅 VALUE 时返回 (key, value)，否则 ("", "")。

    新代码请使用 resolve_field 以区分 ABSENT / EMPTY。
    """
    status, key, value = resolve_field(facts, *candidates)
    if status is FieldStatus.VALUE:
        return key, value
    return "", ""
