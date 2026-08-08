"""End-to-end CHECK against a realistic form payload (user sample shape)."""
from __future__ import annotations

import json
import unittest

from dd_check.config import Settings
from dd_check.models import CheckRequest, FindingStatus
from dd_check.orchestrator import Orchestrator


def _user_like_payload() -> dict:
    result = [
        {
            "label": "基本信息",
            "code": "baseInfo",
            "type": "simple-info-display",
            "value": [{"尽职调查ID": "RRS2607081600028"}, {"发起时间": "2026-07-08 16:30:17"}],
        },
        {
            "label": "客户前一风险等级",
            "code": "customerPreviousRiskLevel",
            "type": "item-info-display",
            "value": [{"客户前一风险等级": "C2"}],
        },
        {
            "label": "客户当前风险等级",
            "code": "customerCurrentRiskLevel",
            "type": "item-info-display",
            "value": [{"客户当前风险等级": "BY"}],
        },
        {
            "label": "客户基本信息",
            "code": "custInfo",
            "type": "tabled-input",
            "value": [{"客户名称": "余**"}, {"客户号": "7563675677"}],
        },
        {
            "label": "客户基本信息",
            "code": "custInfoEdit",
            "type": "tabled-input",
            "value": [
                {"性别": ""},
                {"国籍": "中国"},
                {"联系方式": "151*****520"},
                {"职业": "摄像头测试员"},
                {"证件种类": "居民身份证"},
                {"证件号码": "33012321448412211541"},
                {"证件有效期起始日": ""},
                {"证件有效期到期日": "2035-01-01"},
                {"工作单位": "海康威视"},
                {"住所地或者工作单位地址 ": "杭***江***花***"},
            ],
        },
        {
            "label": "交易对手信息",
            "code": "counterParties",
            "type": "table-display",
            "value": [
                {"交易对手名称": "76551562455222e大众点评吗", "交易金额": "37.5", "交易笔数": "3"},
                {"交易对手名称": "76551562455222e饿了吗", "交易金额": "27.5", "交易笔数": "2"},
            ],
        },
        {
            "label": "客户信息是否真实、完整、有效？",
            "code": "authenticity",
            "type": "explained-check-box",
            "value": [
                {
                    "是，客户身份信息和身份资料真实、有效、完整，不存在缺失、错误、矛盾、不一致等异常情形，无合理理由怀疑客户身份信息失效或不真实": "否",
                    "补充说明": "",
                },
                {
                    "否，客户身份信息和身份资料存在缺失、错误、矛盾、不一致、失效等异常情形，应视风险状况采取相应的尽职调查和风险管理措施": "否",
                    "补充说明": "",
                },
            ],
        },
        {
            "label": "客户当前身份背景的识别情况",
            "code": "explainContent1",
            "type": "multi-line-input",
            "value": [{"客户当前身份背景的识别情况": ""}],
        },
        {
            "label": "先前客户账户风险成因及特征",
            "code": "explainContent2",
            "type": "multi-line-input",
            "value": [{"先前客户账户风险成因及特征": ""}],
        },
        {
            "label": "现客户账户风险评估情况",
            "code": "explainContent3",
            "type": "multi-line-input",
            "value": [{"现客户账户风险评估情况": ""}],
        },
    ]
    return {
        "reportId": "WSS2606120900003",
        "investId": "",
        "result": json.dumps(result, ensure_ascii=False),
        "question": "请帮我检查这份尽职调查报告的回答内容是否有明细错误的地方",
        "busCode": "vsdbvsb",
        "busCodeDesc": "vzbvxc",
        "currentDateTime": "2026-07-09 06:36:46",
        "custType": "p",
        "approveData": "",
        "phase": "CHECK",
        "bankId": "vavsazb",
    }


class OrchestratorCheckTests(unittest.TestCase):
    def test_user_sample_finds_issues_and_scores(self):
        orch = Orchestrator(Settings())
        req = CheckRequest.model_validate(_user_like_payload())
        result = orch.check_one(req)
        self.assertEqual(result.reportId, "WSS2606120900003")
        self.assertEqual(result.custType, "PRIVATE")
        self.assertEqual(result.phase, "CHECK")
        self.assertTrue(result.enabled_dimensions)
        statuses = {f.status for f in result.findings}
        self.assertIn(FindingStatus.FAIL, statuses)
        dims = {f.dimension for f in result.findings if f.status == FindingStatus.FAIL}
        # checkbox all-no + empty narratives / id format should fire
        self.assertTrue(
            {"checkbox_consistency", "logic_consistency", "id_validity"} & dims
            or {"checkbox_consistency", "logic_consistency"} & dims
        )
        self.assertGreaterEqual(result.score, 0)
        self.assertLessEqual(result.score, 100)
        self.assertIn(result.grade, list("ABCDE"))
        # writing should catch 大众点评吗 / 饿了吗
        writing_msgs = [f.message for f in result.findings if f.dimension == "writing"]
        self.assertTrue(any("点评" in m or "饿了" in m or "错别字" in m for m in writing_msgs))

    def test_recheck_requires_approve_data(self):
        orch = Orchestrator(Settings())
        payload = _user_like_payload()
        payload["phase"] = "RECHECK"
        payload["approveData"] = ""
        result = orch.check_one(CheckRequest.model_validate(payload))
        approval = [f for f in result.findings if f.dimension == "approval_compliance"]
        self.assertTrue(any(f.status == FindingStatus.FAIL for f in approval))

    def test_batch_aggregate(self):
        orch = Orchestrator(Settings())
        item = CheckRequest.model_validate(_user_like_payload())
        out = orch.check_batch(
            __import__("dd_check.models", fromlist=["BatchCheckRequest"]).BatchCheckRequest(
                items=[item, item.model_copy(update={"reportId": "R2"})],
                phase="RECHECK",
            )
        )
        self.assertEqual(out["count"], 2)
        self.assertIn("aggregate_summary", out)


if __name__ == "__main__":
    unittest.main()
