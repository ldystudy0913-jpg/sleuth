"""规则引擎：按策略启用的维度逐一检查，产出 Finding 列表。

节点 run_rule_dims 会构造 RuleContext 并调用 RuleEngine.run(dims, ctx)。
各 check_* 函数对应一个维度名（与策略 JSON 里 dimensions 对齐）。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Dict, List, Optional, Protocol

from ..config import Settings
from ..models import (
    CustType,
    Finding,
    FindingStatus,
    Phase,
    ReportFacts,
    Severity,
)
from ..adapter import find_field
from ..attachments import AttachmentBundle


class RuleContext:
    """单次规则运行上下文：表单事实 + 附件摘要 + 客户/阶段等。"""

    def __init__(
        self,
        *,
        facts: ReportFacts,
        settings: Settings,
        cust_type: CustType,
        phase: Phase,
        current_datetime: str,
        approve_data: str,
        attachments: Optional[AttachmentBundle] = None,
        question: str = "",
    ):
        self.facts = facts
        self.settings = settings
        self.cust_type = cust_type
        self.phase = phase
        self.current_datetime = current_datetime
        self.approve_data = approve_data or ""
        self.attachments = attachments
        self.question = question or ""


RuleFn = Callable[[RuleContext], List[Finding]]


def _fail(dimension: str, message: str, evidence: str = "", suggestion: str = "", field_code: str = "") -> Finding:
    return Finding(
        dimension=dimension,
        status=FindingStatus.FAIL,
        severity=Severity.FAIL,
        message=message,
        evidence=evidence,
        suggestion=suggestion,
        field_code=field_code,
    )


def _warn(dimension: str, message: str, evidence: str = "", suggestion: str = "", field_code: str = "") -> Finding:
    return Finding(
        dimension=dimension,
        status=FindingStatus.WARN,
        severity=Severity.WARN,
        message=message,
        evidence=evidence,
        suggestion=suggestion,
        field_code=field_code,
    )


def _pass(dimension: str, message: str = "ok") -> Finding:
    return Finding(dimension=dimension, status=FindingStatus.PASS, severity=Severity.INFO, message=message)


def _skip(dimension: str, message: str) -> Finding:
    return Finding(dimension=dimension, status=FindingStatus.SKIP, severity=Severity.INFO, message=message)


# ---- writing ----

_TYPO_PATTERNS = [
    (re.compile(r"大众点评吗"), "疑似错别字：大众点评吗 → 大众点评"),
    (re.compile(r"饿了吗"), "疑似错别字：饿了吗 → 饿了么"),
]


def check_writing(ctx: RuleContext) -> List[Finding]:
    """书写质量：错别字、叙述是否空。"""
    findings: List[Finding] = []
    blob_parts = list(ctx.facts.fields.values())
    for rows in ctx.facts.tables.values():
        for row in rows:
            blob_parts.extend(row.values())
    blob = "\n".join(blob_parts)
    for pat, msg in _TYPO_PATTERNS:
        m = pat.search(blob)
        if m:
            findings.append(_warn("writing", msg, evidence=m.group(0), suggestion="修正错别字"))
    # empty narrative when question asks for content check — still soft warn if all narratives empty
    empty_narr = []
    for code in ctx.facts.narrative_codes:
        val = ctx.facts.fields.get(code, "")
        if not val.strip():
            empty_narr.append(code)
    if empty_narr and len(empty_narr) == len(ctx.facts.narrative_codes) and ctx.facts.narrative_codes:
        findings.append(
            _warn(
                "writing",
                "多处叙述性说明为空",
                evidence=",".join(empty_narr),
                suggestion="补充客户身份背景、风险成因及评估说明",
            )
        )
    if not findings:
        findings.append(_pass("writing"))
    return findings


# ---- basic info completeness ----

_REQUIRED_PRIVATE = [
    ("客户名称", "客户名称"),
    ("客户号", "客户号"),
    ("国籍", "国籍"),
    ("证件种类", "证件种类"),
    ("证件号码", "证件号码"),
]


def check_basic_info_completeness(ctx: RuleContext) -> List[Finding]:
    """基本信息必填完整性。"""
    findings: List[Finding] = []
    for label, key in _REQUIRED_PRIVATE:
        k, v = find_field(ctx.facts, key, label)
        if not v.strip():
            findings.append(
                _fail(
                    "basic_info_completeness",
                    f"缺少必填字段：{label}",
                    field_code=k or key,
                    suggestion=f"请补全{label}",
                )
            )
    # gender / phone soft
    for label in ("性别", "联系方式", "职业", "住所地或者工作单位地址"):
        k, v = find_field(ctx.facts, label)
        if not v.strip():
            findings.append(
                _warn(
                    "basic_info_completeness",
                    f"字段为空：{label}",
                    field_code=k or label,
                    suggestion=f"建议补全{label}",
                )
            )
    if not any(f.status != FindingStatus.PASS for f in findings):
        findings.append(_pass("basic_info_completeness"))
    return findings


# ---- id validity ----

_ID18 = re.compile(r"^\d{17}[\dXx]$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def check_id_validity(ctx: RuleContext) -> List[Finding]:
    """证件号码格式/有效期。"""
    findings: List[Finding] = []
    _, id_type = find_field(ctx.facts, "证件种类")
    _, id_no = find_field(ctx.facts, "证件号码")
    _, start = find_field(ctx.facts, "证件有效期起始日")
    _, end = find_field(ctx.facts, "证件有效期到期日")

    if id_type and "身份证" in id_type:
        if not id_no:
            findings.append(_fail("id_validity", "身份证号码缺失", field_code="证件号码"))
        elif not _ID18.match(id_no.strip()):
            findings.append(
                _fail(
                    "id_validity",
                    "居民身份证号码格式不正确（应为18位）",
                    evidence=id_no,
                    field_code="证件号码",
                    suggestion="核对证件号码位数与校验位",
                )
            )
        elif len(id_no.strip()) == 18:
            # very rough: birth date segment
            birth = id_no[6:14]
            try:
                datetime.strptime(birth, "%Y%m%d")
            except ValueError:
                findings.append(
                    _warn("id_validity", "身份证号码中的出生日期段无效", evidence=birth, field_code="证件号码")
                )

    if not start.strip():
        findings.append(_warn("id_validity", "证件有效期起始日为空", field_code="证件有效期起始日"))
    if not end.strip():
        findings.append(_fail("id_validity", "证件有效期到期日为空", field_code="证件有效期到期日"))
    else:
        m = _DATE.match(end.strip())
        if not m:
            findings.append(_warn("id_validity", "证件到期日格式无法解析", evidence=end))
        else:
            try:
                end_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                ref = ctx.current_datetime
                ref_dt = None
                if ref:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            ref_dt = datetime.strptime(ref[:19], fmt)
                            break
                        except ValueError:
                            continue
                if ref_dt and end_dt.date() < ref_dt.date():
                    findings.append(
                        _fail(
                            "id_validity",
                            "证件已过期",
                            evidence=f"到期日={end}, 当前={ref}",
                            suggestion="更新有效证件",
                        )
                    )
            except ValueError:
                findings.append(_warn("id_validity", "证件到期日非法", evidence=end))

    if not findings:
        findings.append(_pass("id_validity"))
    return findings


# ---- address ----

def check_address_validity(ctx: RuleContext) -> List[Finding]:
    """地址规范性。"""
    findings: List[Finding] = []
    k, addr = find_field(ctx.facts, "住所地或者工作单位地址", "住所地", "工作单位地址", "地址")
    if not addr.strip():
        findings.append(_fail("address_validity", "地址为空", field_code=k or "地址"))
    elif len(addr.strip()) < 6:
        findings.append(_warn("address_validity", "地址过短，可能不完整", evidence=addr, field_code=k))
    elif "*" in addr and addr.count("*") >= 3:
        findings.append(
            _warn(
                "address_validity",
                "地址大量脱敏，无法核验完整性",
                evidence=addr,
                suggestion="在权限允许范围内核验完整地址",
            )
        )
    if not findings:
        findings.append(_pass("address_validity"))
    return findings


# ---- checkbox consistency ----

def check_checkbox_consistency(ctx: RuleContext) -> List[Finding]:
    """勾选框与说明文字一致性。"""
    findings: List[Finding] = []
    for code, rows in ctx.facts.checkboxes.items():
        yes_no_selected = []
        for row in rows:
            # each option has a label key and 是/否 value
            for opt_label, val in row.items():
                if opt_label in ("补充说明",):
                    continue
                if val in ("是", "否", "true", "false", "Y", "N"):
                    yes_no_selected.append((opt_label, val))
        if not yes_no_selected:
            continue
        # explained-check-box: exactly one of the main options should be 是
        positives = [x for x in yes_no_selected if x[1] in ("是", "true", "Y")]
        if len(positives) == 0:
            findings.append(
                _fail(
                    "checkbox_consistency",
                    f"选项组未作有效选择（全部为否或未选）：{code}",
                    evidence=str(yes_no_selected[:4]),
                    field_code=code,
                    suggestion="请在互斥选项中选择一项并填写补充说明",
                )
            )
        elif len(positives) > 1 and code in ("authenticity", "supplementaryReport", "unfollowedRisk", "adoptMeasure"):
            findings.append(
                _warn(
                    "checkbox_consistency",
                    f"选项组出现多项「是」，可能互斥冲突：{code}",
                    evidence=str(positives),
                    field_code=code,
                )
            )
    if not findings:
        findings.append(_pass("checkbox_consistency"))
    return findings


# ---- logic consistency ----

def check_logic_consistency(ctx: RuleContext) -> List[Finding]:
    """字段间逻辑一致性（如日期先后）。"""
    findings: List[Finding] = []
    _, prev = find_field(ctx.facts, "客户前一风险等级")
    _, curr = find_field(ctx.facts, "客户当前风险等级")
    # narratives empty but risk high
    empty_required_narr = []
    for code in ("explainContent1", "explainContent2", "explainContent3"):
        val = ctx.facts.fields.get(code, "")
        if code in ctx.facts.narrative_codes or any(code in k for k in ctx.facts.fields):
            # get best value
            _, v = find_field(ctx.facts, code)
            if not (v or val).strip():
                empty_required_narr.append(code)
    if curr and empty_required_narr:
        findings.append(
            _fail(
                "logic_consistency",
                "当前风险等级已填写，但关键尽调说明字段为空，存在答非所问/漏填",
                evidence=f"风险等级={curr}; empty={empty_required_narr}",
                suggestion="按题干补充身份背景、风险成因与评估结论",
            )
        )
    # counterparty name typos already in writing; check duplicate odd names
    rows = ctx.facts.tables.get("counterParties") or []
    for row in rows:
        name = row.get("交易对手名称", "")
        if "吗" in name and ("点评" in name or "饿了" in name):
            findings.append(
                _warn(
                    "logic_consistency",
                    "交易对手名称疑似录入错误",
                    evidence=name,
                    field_code="counterParties",
                )
            )
            break
    # control advice vs account control
    advice = ctx.facts.checkboxes.get("riskControlAdvise") or []
    selected = []
    for row in advice:
        for k, v in row.items():
            if k != "补充说明" and v == "是":
                selected.append(k)
    if selected and not (ctx.facts.tables.get("eacCtlInfo") or []):
        findings.append(
            _warn(
                "logic_consistency",
                "已选择风险管控建议，但账户管控情况表为空",
                evidence=str(selected[:3]),
            )
        )
    if prev and curr and prev == curr:
        # not necessarily wrong
        pass
    if not findings:
        findings.append(_pass("logic_consistency"))
    return findings


# ---- beneficial owner (corporate) ----

def check_beneficial_owner(ctx: RuleContext) -> List[Finding]:
    """受益所有人（对公）。"""
    if ctx.cust_type != CustType.CORPORATE:
        return [_skip("beneficial_owner", "非对公客户，跳过受益所有人检查")]
    findings: List[Finding] = []
    # look for BO related fields / tables
    bo_keys = [k for k in ctx.facts.fields if "受益" in k or "所有人" in k]
    bo_tables = [k for k in ctx.facts.tables if "受益" in k or "beneficial" in k.lower() or "ubo" in k.lower()]
    if not bo_keys and not bo_tables:
        findings.append(
            _fail(
                "beneficial_owner",
                "对公客户未发现受益所有人相关字段或穿透信息",
                suggestion="补充受益所有人识别与穿透说明",
            )
        )
    else:
        empty = True
        for k in bo_keys:
            if ctx.facts.fields.get(k, "").strip():
                empty = False
        for t in bo_tables:
            if ctx.facts.tables.get(t):
                empty = False
        if empty:
            findings.append(_fail("beneficial_owner", "受益所有人信息为空，未完成穿透"))
        else:
            findings.append(_pass("beneficial_owner", "已发现受益所有人相关信息（需人工复核穿透完整性）"))
    return findings


# ---- attachments ----

def check_attachment_presence(ctx: RuleContext) -> List[Finding]:
    """附件是否存在（依赖 AttachmentBundle）。"""
    if ctx.attachments is None:
        return [_skip("attachment_presence", "未启用附件流水线或 investId 为空")]
    if ctx.attachments.skipped:
        return [
            _warn(
                "attachment_presence",
                "部分附件跳过",
                evidence="; ".join(ctx.attachments.skipped[:5]),
            )
        ]
    if not ctx.attachments.excerpts:
        return [_fail("attachment_presence", "策略要求附件检查，但未获取到任何附件内容")]
    return [_pass("attachment_presence", f"已获取 {len(ctx.attachments.excerpts)} 个附件摘要")]


def check_attachment_sanction_geo(ctx: RuleContext) -> List[Finding]:
    """附件文本制裁地/敏感地理关键词。"""
    if ctx.attachments is None or not ctx.attachments.excerpts:
        return [_skip("attachment_sanction_geo", "无附件摘要可检查")]
    findings: List[Finding] = []
    regions = ctx.settings.high_risk_regions
    for ex in ctx.attachments.excerpts:
        text = ex.text or ""
        hits = [r for r in regions if r and r in text]
        if hits:
            findings.append(
                _fail(
                    "attachment_sanction_geo",
                    f"附件疑似涉及高风险国家/地区：{','.join(hits)}",
                    evidence=ex.file_id,
                    suggestion="核实附件合规性并按名单要求处置",
                )
            )
    if not findings:
        findings.append(_pass("attachment_sanction_geo"))
    return findings


def check_attachment_vs_report(ctx: RuleContext) -> List[Finding]:
    """附件摘要与报告字段交叉核对。"""
    if ctx.attachments is None or not ctx.attachments.excerpts:
        return [_skip("attachment_vs_report", "无附件摘要可对照")]
    _, name = find_field(ctx.facts, "客户名称")
    findings: List[Finding] = []
    if name:
        # masked name: compare visible prefix chars
        visible = name.replace("*", "").strip()
        if len(visible) >= 1:
            joined = "\n".join(e.text for e in ctx.attachments.excerpts)
            if visible and visible not in joined and name not in joined:
                findings.append(
                    _warn(
                        "attachment_vs_report",
                        "附件文本中未检索到报告客户名称关键可见字符，可能不一致或附件无关",
                        evidence=f"客户名称={name}",
                    )
                )
    if not findings:
        findings.append(_pass("attachment_vs_report"))
    return findings


# ---- approval ----

def check_approval_compliance(ctx: RuleContext) -> List[Finding]:
    """审批信息合规性。"""
    if ctx.phase != Phase.RECHECK:
        return [_skip("approval_compliance", "非 RECHECK 阶段")]
    raw = (ctx.approve_data or "").strip()
    if not raw:
        return [_fail("approval_compliance", "回检阶段缺少 approveData，无法做审批合规检查")]
    # best-effort JSON
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [_warn("approval_compliance", "approveData 非 JSON，仅做非空校验通过", evidence=raw[:80])]
    findings: List[Finding] = []
    nodes = data if isinstance(data, list) else data.get("nodes") or data.get("approvals") or []
    if not nodes:
        findings.append(_fail("approval_compliance", "审批节点列表为空"))
        return findings
    # duration checks if timestamps present
    times = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        opinion = str(n.get("opinion") or n.get("comment") or "").strip()
        if not opinion:
            findings.append(_warn("approval_compliance", "存在空审批意见的节点", evidence=str(n.get("node") or n.get("name") or "")))
        for key in ("time", "approveTime", "endTime", "timestamp"):
            if n.get(key):
                times.append(str(n.get(key)))
    if len(times) >= 2:
        parsed = []
        for t in times:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed.append(datetime.strptime(t[:19], fmt))
                    break
                except ValueError:
                    continue
        if len(parsed) >= 2:
            parsed.sort()
            delta = (parsed[-1] - parsed[0]).total_seconds()
            if delta < 60:
                findings.append(
                    _warn(
                        "approval_compliance",
                        "审批首末节点间隔过短，存在审批不认真嫌疑",
                        evidence=f"{delta}s",
                    )
                )
            if delta > 30 * 24 * 3600:
                findings.append(
                    _warn(
                        "approval_compliance",
                        "审批周期过长，效率偏低",
                        evidence=f"{delta}s",
                    )
                )
    if not findings:
        findings.append(_pass("approval_compliance"))
    return findings


REGISTRY: Dict[str, RuleFn] = {
    "writing": check_writing,
    "basic_info_completeness": check_basic_info_completeness,
    "id_validity": check_id_validity,
    "address_validity": check_address_validity,
    "checkbox_consistency": check_checkbox_consistency,
    "logic_consistency": check_logic_consistency,
    "beneficial_owner": check_beneficial_owner,
    "attachment_presence": check_attachment_presence,
    "attachment_sanction_geo": check_attachment_sanction_geo,
    "attachment_vs_report": check_attachment_vs_report,
    "approval_compliance": check_approval_compliance,
}


class RuleEngine:
    """维度名 → 检查函数；未知维度记 SKIP finding。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, dimensions: List[str], ctx: RuleContext) -> List[Finding]:
        """按 dimensions 顺序执行并合并 findings。"""
        out: List[Finding] = []
        for dim in dimensions:
            fn = REGISTRY.get(dim)
            if fn is None:
                out.append(_skip(dim, f"未知检查维度：{dim}"))
                continue
            out.extend(fn(ctx))
        return out
