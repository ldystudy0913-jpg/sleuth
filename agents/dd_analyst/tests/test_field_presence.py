"""Field presence (ABSENT/EMPTY/VALUE) and parse stability tests."""
from __future__ import annotations

import json
import unittest

from dd_check.adapter import FieldStatus, ReportAdapter, find_field, resolve_field
from dd_check.config import Settings
from dd_check.models import CheckRequest, FindingStatus
from dd_check.orchestrator import Orchestrator
from dd_check.rules import (
    RuleContext,
    check_basic_info_completeness,
    check_beneficial_owner,
)
from dd_check.models import CustType, Phase


SUSPICIOUS_RESULT = [
    {
        "label": "基本信息",
        "code": "baseInfo",
        "type": "simple-info-display",
        "value": [
            {"尽调ID": "DD202606240000000020"},
            {"客户号": "123"},
            {"客户名称": "21211"},
            {"客户来源": "核心系统"},
            {"客户类型": "零售"},
            {"尽调模式": "KYC网点反洗钱管理员"},
            {"签发人": "邓宏成/325128"},
            {"签发日期": "2026-06-24"},
            {"签发机构": "总行/信息技术部"},
            {"接收机构": "深圳分行"},
            {"要求反馈日期": "2026-06-30"},
            {"完成人": "陈平/002544"},
            {"户口号": "12***052121"},
            {"是否关联可疑宗": "否"},
            {"客户是否缺失": "否"},
            {"客户是否配合": "是"},
        ],
    },
    {
        "label": "可疑特征描述",
        "code": "susFeature",
        "type": "item-info-display",
        "value": [
            {"SF0000000000000000000001": "多笔固定金额"},
            {"SF0000000000000000000002": "深夜转账"},
        ],
    },
    {
        "label": "尽调问题及答案",
        "code": "dueDilQuestion",
        "type": "dynamic-qa-input",
        "value": [{"交易的目的是什么": "122212121212121"}],
    },
]


class ResolveFieldTests(unittest.TestCase):
    def test_absent_vs_empty_vs_value(self):
        raw = [
            {
                "label": "客户基本信息",
                "code": "cust",
                "type": "tabled-input",
                "value": [{"国籍": "中国"}, {"性别": ""}, {"无关名称备注": "有值"}],
            }
        ]
        facts = ReportAdapter().parse(json.dumps(raw, ensure_ascii=False))
        st, k, v = resolve_field(facts, "国籍")
        self.assertEqual(st, FieldStatus.VALUE)
        self.assertEqual(v, "中国")
        st, k, v = resolve_field(facts, "性别")
        self.assertEqual(st, FieldStatus.EMPTY)
        self.assertEqual(k, "性别")
        st, k, v = resolve_field(facts, "客户名称")
        self.assertEqual(st, FieldStatus.ABSENT)
        # no greedy substring: 无关名称备注 must not satisfy 客户名称
        self.assertEqual(find_field(facts, "客户名称"), ("", ""))

    def test_trailing_space_key_normalized(self):
        raw = [
            {
                "label": "地址",
                "code": "addr",
                "type": "tabled-input",
                "value": [{"住所地或者工作单位地址 ": "杭州市西湖区文三路1号"}],
            }
        ]
        facts = ReportAdapter().parse(json.dumps(raw, ensure_ascii=False))
        st, k, v = resolve_field(facts, "住所地或者工作单位地址")
        self.assertEqual(st, FieldStatus.VALUE)
        self.assertIn("杭州", v)

    def test_suspicious_report_stable_and_complete(self):
        payload = json.dumps(SUSPICIOUS_RESULT, ensure_ascii=False)
        a = ReportAdapter().parse(payload)
        b = ReportAdapter().parse(payload)
        self.assertEqual(set(a.fields.keys()), set(b.fields.keys()))
        expected = {
            "尽调ID",
            "客户号",
            "客户名称",
            "客户来源",
            "客户类型",
            "尽调模式",
            "签发人",
            "签发日期",
            "签发机构",
            "接收机构",
            "要求反馈日期",
            "完成人",
            "户口号",
            "是否关联可疑宗",
            "客户是否缺失",
            "客户是否配合",
            "SF0000000000000000000001",
            "SF0000000000000000000002",
            "交易的目的是什么",
        }
        self.assertTrue(expected.issubset(set(a.fields.keys())))
        self.assertEqual(resolve_field(a, "客户名称")[0], FieldStatus.VALUE)
        self.assertEqual(resolve_field(a, "证件号码")[0], FieldStatus.ABSENT)
        self.assertEqual(resolve_field(a, "国籍")[0], FieldStatus.ABSENT)


class PresenceGatedRulesTests(unittest.TestCase):
    def test_basic_info_skips_absent_flags_empty(self):
        raw = [
            {
                "label": "客户",
                "code": "c",
                "type": "tabled-input",
                "value": [{"客户名称": "张三"}, {"客户号": "1"}, {"性别": ""}],
            }
        ]
        facts = ReportAdapter().parse(json.dumps(raw, ensure_ascii=False))
        ctx = RuleContext(
            facts=facts,
            settings=Settings(),
            cust_type=CustType.PRIVATE,
            phase=Phase.CHECK,
            current_datetime="2026-07-09 06:36:46",
            approve_data="",
        )
        findings = check_basic_info_completeness(ctx)
        msgs = [f.message for f in findings]
        self.assertFalse(any("国籍" in m for m in msgs))
        self.assertFalse(any("证件" in m for m in msgs))
        self.assertTrue(any("性别" in m and "空" in m for m in msgs))
        self.assertFalse(any("缺少必填" in m for m in msgs))

    def test_beneficial_owner_absent_is_skip(self):
        facts = ReportAdapter().parse(
            json.dumps(
                [
                    {
                        "label": "基本",
                        "code": "b",
                        "type": "simple-info-display",
                        "value": [{"客户名称": "某公司"}],
                    }
                ],
                ensure_ascii=False,
            )
        )
        ctx = RuleContext(
            facts=facts,
            settings=Settings(),
            cust_type=CustType.CORPORATE,
            phase=Phase.CHECK,
            current_datetime="",
            approve_data="",
        )
        findings = check_beneficial_owner(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, FindingStatus.SKIP)

    def test_suspicious_e2e_no_missing_required_fail(self):
        orch = Orchestrator(Settings())
        req = CheckRequest.model_validate(
            {
                "reportId": "11111",
                "investId": "TSK020260612ehJDJkkl",
                "result": json.dumps(SUSPICIOUS_RESULT, ensure_ascii=False),
                "question": "报告有什么问题",
                "busCode": "AML",
                "busCodeDesc": "可疑",
                "currentDateTime": "2026-05-09",
                "custType": "WSL",
                "approveData": "[]",
                "phase": "CHECK",
                "bankId": "",
            }
        )
        result = orch.check_one(req)
        basic_fails = [
            f
            for f in result.findings
            if f.dimension == "basic_info_completeness" and f.status == FindingStatus.FAIL
        ]
        self.assertFalse(
            any("国籍" in f.message or "证件" in f.message or "缺少必填" in f.message for f in basic_fails)
        )


if __name__ == "__main__":
    unittest.main()
