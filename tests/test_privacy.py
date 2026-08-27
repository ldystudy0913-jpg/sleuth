"""Output desensitization (PII scrub) tests."""
from __future__ import annotations

import unittest

from sleuth.privacy import contains_raw_pii, desensitize_text, maybe_desensitize
from sleuth.tools.base import ToolResult


class TestPrivacy(unittest.TestCase):
    def test_id_card(self) -> None:
        raw = "证件号 110101199001011234 已核验"
        out = desensitize_text(raw)
        self.assertNotIn("110101199001011234", out)
        self.assertIn("110", out)
        self.assertIn("34", out)
        self.assertIn("*", out)

    def test_mobile(self) -> None:
        raw = "联系电话 13812345678"
        out = desensitize_text(raw)
        self.assertNotIn("13812345678", out)
        self.assertEqual(out, "联系电话 138****5678")

    def test_bank_card(self) -> None:
        raw = "卡号 6222021234567890123"
        out = desensitize_text(raw)
        self.assertNotIn("6222021234567890123", out)
        self.assertTrue(out.endswith("0123") or "0123" in out)
        self.assertIn("*", out)

    def test_password_label(self) -> None:
        raw = "密码：Secret123 请勿泄露"
        out = desensitize_text(raw)
        self.assertNotIn("Secret123", out)
        self.assertIn("密码", out)
        self.assertIn("***", out)

    def test_labeled_address(self) -> None:
        raw = "家庭住址：北京市朝阳区某某街道1号楼101室"
        out = desensitize_text(raw)
        self.assertNotIn("朝阳区", out)
        self.assertIn("家庭住址：", out)
        self.assertIn("***", out)

    def test_already_masked_unchanged(self) -> None:
        raw = "电话 138****5678 证件 110***********34"
        out = desensitize_text(raw)
        self.assertEqual(out, raw)

    def test_business_scope_address_word_not_over_masked(self) -> None:
        # no 「地址：」label with long value — should not wipe the sentence
        raw = "经营范围：互联网信息服务、软件开发"
        out = desensitize_text(raw)
        self.assertEqual(out, raw)

    def test_maybe_desensitize_flag(self) -> None:
        raw = "手机 13900001111"
        self.assertNotIn("13900001111", maybe_desensitize(raw, enabled=True))
        self.assertIn("13900001111", maybe_desensitize(raw, enabled=False))

    def test_scrub_tool_result_shape(self) -> None:
        from sleuth.config import Config

        cfg = Config(output_desensitize=True)
        result = ToolResult.success("ok", "客户手机 13711112222")
        scrubbed = ToolResult(
            title=desensitize_text(result.title),
            output=desensitize_text(result.output),
            metadata=result.metadata,
            is_error=result.is_error,
            attachments=result.attachments,
        )
        self.assertNotIn("13711112222", scrubbed.output)
        self.assertTrue(cfg.output_desensitize)

    def test_http_urls_not_masked_as_bank_or_mobile(self) -> None:
        url = "https://kb.example/doc/6222021234567890123/file.pdf?id=13812345678"
        raw = f"《风险点手册.pdf》：{url}"
        out = desensitize_text(raw)
        self.assertEqual(out, raw)
        self.assertFalse(contains_raw_pii(raw))

    def test_bank_card_outside_url_still_masked(self) -> None:
        raw = "卡号 6222021234567890123 见 https://kb.example/doc/6222021234567890123.pdf"
        out = desensitize_text(raw)
        self.assertNotIn("卡号 6222021234567890123", out)
        self.assertIn("https://kb.example/doc/6222021234567890123.pdf", out)
        self.assertTrue(contains_raw_pii("110101199001011234"))
        self.assertFalse(contains_raw_pii(desensitize_text("110101199001011234")))


if __name__ == "__main__":
    unittest.main()
