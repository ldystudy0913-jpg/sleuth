"""Local pipeline tests with mocked remote KB (no local risk JSON)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dd_reply.config import Settings
from dd_reply.kb.remote import KbHit, RiskRetrieval
from dd_reply.models import FrameworkRequest
from dd_reply.pipeline import generate_framework


def _hit(code: str = "C001") -> KbHit:
    return KbHit.from_dict(
        {
            "id": "1",
            "title": "受益所有人识别",
            "paragraph": f"{code} 请核实受益所有人。判断要点：核对证件。对应材料：身份证。",
            "fileName": "风险点手册.pdf",
            "fileUrl": "https://kb.example/files/risk-manual.pdf",
            "knowledgeId": "10752",
            "rankScore": 0.9,
            "comprehended": 1,
            "finalResponse": 1,
        }
    )


def _settings() -> Settings:
    return Settings(kb_api_url="http://kb.test/search", kb_top_k=8)


class TestPipelineLocal(unittest.TestCase):
    def test_requires_kb_api_url(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            generate_framework(
                FrameworkRequest(risk_codes=["C001"]),
                settings=Settings(kb_api_url=""),
                use_llm=False,
            )
        self.assertIn("DD_REPLY_KB_API_URL", str(ctx.exception))

    def test_fallback_multi_risk_and_slots(self) -> None:
        def fake_retrieve(codes, settings, **kwargs):
            out = []
            for c in codes:
                cu = str(c).upper()
                if cu == "C001":
                    out.append(RiskRetrieval(code=cu, question=cu, hits=[_hit(cu)]))
                else:
                    out.append(
                        RiskRetrieval(code=cu, question=cu, hits=[], error="empty_hits")
                    )
            return out

        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "note.txt"
            note.write_text("访谈：法人表示了解开户用途。", encoding="utf-8")
            req = FrameworkRequest(
                risk_codes=["C001", "C999"],
                customer_name="测试科技有限公司",
                established_at="2019-05-01",
                business_scope="软件开发",
                local_paths=[str(note)],
            )
            with patch("dd_reply.pipeline.retrieve_risk_codes", side_effect=fake_retrieve):
                result = generate_framework(req, settings=_settings(), use_llm=False)
            self.assertIn("预分析", result.markdown)
            self.assertIn("C001", result.markdown)
            self.assertIn("C999", result.markdown)
            self.assertIn("待核实", result.markdown)
            self.assertIn("最终判定由人工作出", result.markdown)
            self.assertIn("知识来源", result.markdown)
            self.assertIn("风险点手册.pdf", result.markdown)
            self.assertEqual(result.meta.get("found_codes"), ["C001"])
            self.assertEqual(result.meta.get("missing_codes"), ["C999"])
            self.assertGreaterEqual(result.meta.get("attachment_count", 0), 1)
            self.assertFalse(result.meta.get("llm_used"))

    def test_mock_llm_path(self) -> None:
        def _mock(_messages):
            return (
                "## 1. 预分析\n字段齐全时可作初步判断。\n"
                "## 2. 答复正文框架\nC007 正文【待核实1：____】\n"
                "## 3. 待核实清单\n- 【待核实1】（C007）需了解法人是否知悉用途；建议方式：访谈；填写格式：问答摘要\n"
                "## 4. 结论判定指引\n- 可排除：访谈一致\n"
            )

        def fake_retrieve(codes, settings, **kwargs):
            return [
                RiskRetrieval(code=str(c).upper(), question=str(c).upper(), hits=[_hit(str(c))])
                for c in codes
            ]

        req = FrameworkRequest(
            risk_codes=["C007"],
            customer_name="甲公司",
            ubo_info="张三",
        )
        with patch("dd_reply.pipeline.retrieve_risk_codes", side_effect=fake_retrieve):
            result = generate_framework(
                req, settings=_settings(), mock_llm=_mock, use_llm=True
            )
        self.assertTrue(result.meta.get("llm_used"))
        self.assertIn("预分析", result.markdown)
        self.assertTrue(result.verification_list)

    def test_requires_risk_query(self) -> None:
        with self.assertRaises(ValueError):
            generate_framework(
                FrameworkRequest(risk_codes=[], risk_names=[]),
                settings=_settings(),
                use_llm=False,
            )

    def test_search_by_name_only(self) -> None:
        captured = []

        def fake_retrieve(codes, settings, **kwargs):
            captured.extend(codes)
            return [
                RiskRetrieval(code=c, question=c, hits=[_hit(c)])
                for c in codes
            ]

        req = FrameworkRequest(
            risk_names=["行政处罚记录"],
            customer_name="某某公司",
        )
        with patch("dd_reply.pipeline.retrieve_risk_codes", side_effect=fake_retrieve):
            result = generate_framework(req, settings=_settings(), use_llm=False)
        self.assertEqual(captured, ["行政处罚记录"])
        self.assertIn("行政处罚记录", result.meta.get("found_codes", []))


if __name__ == "__main__":
    unittest.main()
