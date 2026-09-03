"""Root-level smoke tests for the scaffold-generated dd_check agent."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1] / "agents" / "dd_check"
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from dd_check.rubric import aggregate_score  # noqa: E402


class DdCheckAgentTests(unittest.TestCase):
    def test_score_3_and_4_is_3_5(self):
        rubric = {
            "score": {"max": 5, "decimals": 1},
            "word": {"filename": "x_{score}_{date}.docx", "date_format": "%Y%m%d"},
            "dimensions": [
                {"id": "logic_consistency", "weight": 1.0},
                {"id": "completeness", "weight": 1.0},
            ],
        }
        self.assertEqual(
            aggregate_score({"logic_consistency": 3, "completeness": 4}, rubric),
            3.5,
        )


if __name__ == "__main__":
    unittest.main()
