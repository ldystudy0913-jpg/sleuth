"""KB lookup + lexicon load tests."""
from __future__ import annotations

import unittest

from dd_reply.kb import list_lexicon, list_risk_codes, load_kb


class TestKbLookup(unittest.TestCase):
    def test_load_seed_kb(self) -> None:
        kb = load_kb()
        self.assertIn("C001", kb.risk_points)
        self.assertIn("C012", kb.risk_points)
        self.assertGreaterEqual(len(kb.lexicon), 15)
        self.assertTrue(any(r.level == "hard" for r in kb.lexicon))
        self.assertTrue(any(r.level == "soft" for r in kb.lexicon))

    def test_single_and_multi_and_missing(self) -> None:
        kb = load_kb()
        found, missing = kb.lookup_risks(["C001"])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "C001")
        self.assertEqual(missing, [])

        found, missing = kb.lookup_risks(["C001", "c003", "C001", "C999"])
        self.assertEqual([r.code for r in found], ["C001", "C003"])
        self.assertEqual(missing, ["C999"])

    def test_list_helpers(self) -> None:
        kb = load_kb()
        codes = list_risk_codes(kb)
        self.assertIn("C007", codes)
        lex = list_lexicon(kb)
        self.assertTrue(any(x.get("id") == 15 for x in lex))


if __name__ == "__main__":
    unittest.main()
