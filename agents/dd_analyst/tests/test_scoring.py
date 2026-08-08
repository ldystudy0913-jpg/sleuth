"""Scoring unit tests."""
from __future__ import annotations

import unittest

from dd_check.config import Settings
from dd_check.models import Finding, FindingStatus, Severity
from dd_check.scoring import aggregate_score


class ScoringTests(unittest.TestCase):
    def test_all_pass(self):
        findings = [
            Finding(dimension="writing", status=FindingStatus.PASS, severity=Severity.INFO, message="ok"),
            Finding(dimension="id_validity", status=FindingStatus.PASS, severity=Severity.INFO, message="ok"),
        ]
        score, grade, _, summary = aggregate_score(
            findings, ["writing", "id_validity"], Settings()
        )
        self.assertEqual(score, 100.0)
        self.assertEqual(grade, "A")
        self.assertIn("100", summary)

    def test_fail_lowers(self):
        findings = [
            Finding(dimension="writing", status=FindingStatus.PASS, severity=Severity.INFO, message="ok"),
            Finding(dimension="id_validity", status=FindingStatus.FAIL, severity=Severity.FAIL, message="bad"),
        ]
        score, grade, dims, _ = aggregate_score(
            findings, ["writing", "id_validity"], Settings()
        )
        self.assertLess(score, 100)
        self.assertIn(grade, list("CDE"))


if __name__ == "__main__":
    unittest.main()
