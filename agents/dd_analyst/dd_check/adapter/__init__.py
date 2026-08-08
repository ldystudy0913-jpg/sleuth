"""表单适配：把业务字段 result（JSON 字符串）解析成 ReportFacts。

节点 parse_report 调用 ReportAdapter().parse(req.result)。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from ..models import ReportFacts, ReportSection

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """去掉 HTML 标签并压缩空白。"""
    t = _HTML_TAG.sub("", text or "")
    return _WS.sub(" ", t).strip()


def _flatten_value_item(item: Any) -> Dict[str, str]:
    """表格/复选一行 value 展平成 str 字典。"""
    if not isinstance(item, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in item.items():
        if isinstance(v, (dict, list)):
            out[str(k).strip()] = json.dumps(v, ensure_ascii=False)
        else:
            out[str(k).strip()] = strip_html(str(v) if v is not None else "")
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
                label=str(raw.get("label") or ""),
                code=str(raw.get("code") or ""),
                type=str(raw.get("type") or ""),
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
                        if not facts.fields.get(code):
                            facts.fields[code] = v
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


def find_field(facts: ReportFacts, *candidates: str) -> Tuple[str, str]:
    """按候选名精确/大小写/子串查找字段，返回 (key, value)。"""
    for cand in candidates:
        if cand in facts.fields and facts.fields[cand]:
            return cand, facts.fields[cand]
    lower_map = {k.lower(): (k, v) for k, v in facts.fields.items()}
    for cand in candidates:
        hit = lower_map.get(cand.lower())
        if hit and hit[1]:
            return hit
    for cand in candidates:
        for k, v in facts.fields.items():
            if cand in k and v:
                return k, v
    return "", ""
