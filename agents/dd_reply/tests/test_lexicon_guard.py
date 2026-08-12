"""Lexicon guard tests (rules from KB)."""
from __future__ import annotations

import unittest

from dd_reply.kb import load_kb
from dd_reply.lexicon_guard import guard_and_rewrite, hard_rules_prompt_block, scan_text


class TestLexiconGuard(unittest.TestCase):
    def test_hard_hit_and_rewrite(self) -> None:
        kb = load_kb()
        text = "本单无需人工核实，可直接通过。"
        scanned = scan_text(text, kb)
        self.assertTrue(scanned.hard_hits)
        self.assertTrue(any(h.rule_id == 15 for h in scanned.hard_hits))
        guarded = guard_and_rewrite(text, kb, rewrite_hard=True)
        self.assertTrue(guarded.rewritten)
        self.assertNotIn("无需人工核实", guarded.text)
        self.assertNotIn("可直接通过", guarded.text)

    def test_soft_hit(self) -> None:
        kb = load_kb()
        text = "现场看下来基本没问题。"
        scanned = scan_text(text, kb)
        self.assertTrue(scanned.soft_hits)
        self.assertFalse(scanned.hard_hits)

    def test_prompt_block_uses_intent(self) -> None:
        block = hard_rules_prompt_block(load_kb())
        self.assertIn("禁用表述", block)
        self.assertIn("类别意图", block)
        self.assertIn("最终判定由人工作出", block)
        self.assertIn("监测规则", block)  # from rule #3 intent

    def test_rule3_night_tx_threshold(self) -> None:
        kb = load_kb()
        text = "该客户夜间交易大于10笔，建议关注。"
        scanned = scan_text(text, kb)
        self.assertTrue(
            any(h.rule_id == 3 for h in scanned.hard_hits),
            msg=f"expected rule 3 hit, got {[ (h.rule_id, h.pattern, h.matched_text) for h in scanned.hard_hits ]}",
        )

    def test_rule2_no_false_positive_on_bare_report(self) -> None:
        kb = load_kb()
        text = "进度已上报领导，等待批复。"
        scanned = scan_text(text, kb)
        self.assertFalse(
            any(h.rule_id == 2 for h in scanned.hard_hits),
            msg="bare「已上报」must not trip rule 2",
        )
        text2 = "差异说明：已上报可疑交易。"
        scanned2 = scan_text(text2, kb)
        self.assertTrue(any(h.rule_id == 2 for h in scanned2.hard_hits))

    def test_rule18_pii_regex(self) -> None:
        kb = load_kb()
        # synthetic ID-shaped / phone-shaped strings for guard tests only
        id_like = "客户证件 110101199001011234 需脱敏"
        phone_like = "联系电话 13812345678"
        masked = "证件 110***********1234，电话 138****5678"
        self.assertTrue(any(h.rule_id == 18 for h in scan_text(id_like, kb).hard_hits))
        self.assertTrue(any(h.rule_id == 18 for h in scan_text(phone_like, kb).hard_hits))
        self.assertFalse(any(h.rule_id == 18 for h in scan_text(masked, kb).hard_hits))

    def test_schema_has_intent_and_regex(self) -> None:
        kb = load_kb()
        rule3 = next(r for r in kb.lexicon if r.id == 3)
        self.assertTrue(rule3.intent)
        self.assertTrue(rule3.banned_regex)
        self.assertEqual(len(kb.lexicon), 20)


if __name__ == "__main__":
    unittest.main()
