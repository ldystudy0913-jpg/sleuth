"""Local pipeline tests with mock LLM / fallback."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dd_reply.models import FrameworkRequest
from dd_reply.pipeline import generate_framework


class TestPipelineLocal(unittest.TestCase):
    def test_fallback_multi_risk_and_slots(self) -> None:
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
            result = generate_framework(req, use_llm=False)
            self.assertIn("预分析", result.markdown)
            self.assertIn("C001", result.markdown)
            self.assertIn("C999", result.markdown)
            self.assertIn("待核实", result.markdown)
            self.assertIn("最终判定由人工作出", result.markdown)
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

        req = FrameworkRequest(
            risk_codes=["C007"],
            customer_name="甲公司",
            ubo_info="张三",
        )
        result = generate_framework(req, mock_llm=_mock, use_llm=True)
        self.assertTrue(result.meta.get("llm_used"))
        self.assertIn("预分析", result.markdown)
        self.assertTrue(result.verification_list)

    def test_requires_risk_codes(self) -> None:
        with self.assertRaises(ValueError):
            generate_framework(FrameworkRequest(risk_codes=[]), use_llm=False)


if __name__ == "__main__":
    unittest.main()
