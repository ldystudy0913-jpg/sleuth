"""Agent Card unit tests (no MCP runtime)."""
from __future__ import annotations

import unittest

from __PKG_NAME__.agent_card import AGENT_NAME, SKILL_MODE, load_agent_card


class TestAgentCardFile(unittest.TestCase):
    def test_card_has_name_and_prompt(self) -> None:
        card = load_agent_card()
        self.assertEqual(card["name"], AGENT_NAME)
        self.assertTrue(card.get("prompt"))
        self.assertEqual(card.get("mode"), "primary")
        self.assertIn("bash", card.get("permission") or {})
        self.assertNotEqual((card.get("permission") or {}).get("bash"), "allow")
        self.assertNotEqual((card.get("permission") or {}).get("edit"), "allow")
        self.assertNotEqual((card.get("permission") or {}).get("write"), "allow")
        self.assertIn(SKILL_MODE, ("private", "cos", "both", "none"))


if __name__ == "__main__":
    unittest.main()
