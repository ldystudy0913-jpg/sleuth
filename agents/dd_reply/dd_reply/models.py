"""请求 / 响应模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re

from pydantic import BaseModel, Field, field_validator


DISCLAIMER = (
    "本输出仅供客户经理与尽调人员参考，最终判定由人工作出；"
    "不得视为自动通过、无需人工核实或终局结论。"
)

_RISK_CODE_RE = re.compile(r"^[A-Za-z]\d{3}$")


def normalize_risk_query(raw: str) -> str:
    """Keep C001-style codes canonical; leave names / free-text as typed."""
    s = str(raw or "").strip()
    if _RISK_CODE_RE.fullmatch(s):
        return s.upper()
    return s


def _normalize_query_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        parts = [p.strip() for p in v.replace("，", ",").split(",")]
        return [normalize_risk_query(p) for p in parts if p.strip()]
    if isinstance(v, list):
        out: List[str] = []
        for x in v:
            q = normalize_risk_query(x)
            if q:
                out.append(q)
        return out
    q = normalize_risk_query(v)
    return [q] if q else []


class FrameworkRequest(BaseModel):
    """生成答复框架的入参。"""

    risk_codes: List[str] = Field(default_factory=list)
    risk_names: List[str] = Field(default_factory=list)
    customer_name: str = ""
    established_at: str = ""
    business_scope: str = ""
    employee_count: str = ""
    registered_capital: str = ""
    annual_revenue: str = ""
    ubo_info: str = ""
    main_business: str = ""
    account_purpose: str = ""
    tx_pattern_estimate: str = ""
    local_paths: List[str] = Field(default_factory=list)
    invest_id: str = ""
    report_id: str = ""
    bank_id: str = ""

    @field_validator("risk_codes", "risk_names", mode="before")
    @classmethod
    def _normalize_codes(cls, v: Any) -> List[str]:
        return _normalize_query_list(v)

    def risk_queries(self) -> List[str]:
        """Search questions: codes and/or names, de-duplicated."""
        seen: set[str] = set()
        out: List[str] = []
        for raw in list(self.risk_codes) + list(self.risk_names):
            q = normalize_risk_query(raw)
            if not q or q in seen:
                continue
            seen.add(q)
            out.append(q)
        return out

    def fields_dict(self) -> Dict[str, str]:
        return {
            "客户名称": self.customer_name,
            "成立时间": self.established_at,
            "经营范围": self.business_scope,
            "员工人数": self.employee_count,
            "注册资本": self.registered_capital,
            "年销售收入": self.annual_revenue,
            "受益所有人身份信息": self.ubo_info,
            "主营业务": self.main_business,
            "开户主要目的": self.account_purpose,
            "账户交易模式预估": self.tx_pattern_estimate,
        }


class VerificationItem(BaseModel):
    slot_id: str
    need_to_know: str = ""
    methods: List[str] = Field(default_factory=list)
    fill_format: str = ""
    related_risk_code: str = ""


class FrameworkResult(BaseModel):
    pre_analysis: str = ""
    reply_body: str = ""
    verification_list: List[VerificationItem] = Field(default_factory=list)
    conclusion_guide: str = ""
    markdown: str = ""
    meta: Dict[str, Any] = Field(default_factory=dict)
