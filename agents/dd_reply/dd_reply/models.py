"""请求 / 响应模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


DISCLAIMER = (
    "本输出仅供客户经理与尽调人员参考，最终判定由人工作出；"
    "不得视为自动通过、无需人工核实或终局结论。"
)


class FrameworkRequest(BaseModel):
    """生成答复框架的入参。"""

    risk_codes: List[str] = Field(default_factory=list)
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

    @field_validator("risk_codes", mode="before")
    @classmethod
    def _normalize_codes(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.replace("，", ",").split(",")]
            return [p for p in parts if p]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()] if str(v).strip() else []

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
