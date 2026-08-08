"""Adapter unit tests."""
from __future__ import annotations

import json
import unittest

from dd_check.adapter import ReportAdapter, find_field


SAMPLE = [
    {
        "label": "客户基本信息",
        "code": "custInfoEdit",
        "type": "tabled-input",
        "value": [
            {"性别": ""},
            {"国籍": "中国"},
            {"证件种类": "居民身份证"},
            {"证件号码": "33012321448412211541"},
            {"证件有效期到期日": "2035-01-01"},
        ],
    },
    {
        "label": "客户信息是否真实、完整、有效？",
        "code": "authenticity",
        "type": "explained-check-box",
        "value": [
            {"是，客户身份信息和身份资料真实": "否", "补充说明": ""},
            {"否，客户身份信息和身份资料存在缺失": "否", "补充说明": ""},
        ],
    },
    {
        "label": "说明",
        "code": "explainContent1",
        "type": "multi-line-input",
        "value": [{"客户当前身份背景的识别情况": ""}],
    },
]


class ReportAdapterTests(unittest.TestCase):
    def test_parse_sections_and_fields(self):
        facts = ReportAdapter().parse(json.dumps(SAMPLE, ensure_ascii=False))
        self.assertEqual(facts.raw_section_count, 3)
        self.assertEqual(find_field(facts, "国籍")[1], "中国")
        self.assertEqual(find_field(facts, "证件号码")[1], "33012321448412211541")
        self.assertIn("authenticity", facts.checkboxes)
        self.assertIn("explainContent1", facts.narrative_codes)

    def test_strip_html(self):
        raw = [
            {
                "label": "原因",
                "code": "ddReason",
                "type": "item-info-display",
                "value": [{"客户强化尽职调查原因": "<p style=\"color:red\">风险BY</p>"}],
            }
        ]
        facts = ReportAdapter().parse(json.dumps(raw, ensure_ascii=False))
        _, v = find_field(facts, "客户强化尽职调查原因")
        self.assertNotIn("<p", v)
        self.assertIn("风险BY", v)

    def test_invalid_json(self):
        with self.assertRaises(json.JSONDecodeError):
            ReportAdapter().parse("{not-json")


if __name__ == "__main__":
    unittest.main()
