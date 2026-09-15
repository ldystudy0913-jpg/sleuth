"""Scenario catalog loading and alias mapping."""
from __future__ import annotations

import unittest
from pathlib import Path

from dd_check.scenarios import (
    get_pack,
    load_catalog,
    match_from_utterance,
    public_catalog,
    resolve_pack,
    resolve_scenario_token,
)

_PACK = Path(__file__).resolve().parents[1]
_CONFIG = _PACK / "config"


class ScenarioCatalogTests(unittest.TestCase):
    def test_catalog_includes_default_and_specials(self) -> None:
        packs = load_catalog(_CONFIG)
        ids = [p.scenario_id for p in packs]
        self.assertEqual(ids[0], "default")
        self.assertIn("corp_onboarding", ids)
        self.assertIn("corp_change", ids)
        default = get_pack(_CONFIG, "default")
        self.assertTrue(default.system_path.is_file())
        self.assertEqual(default.system_path, _CONFIG / "prompts" / "system.md")
        self.assertEqual(default.output, "findings")
        onb = get_pack(_CONFIG, "corp_onboarding")
        self.assertEqual(onb.output, "conflicts")
        self.assertTrue(onb.system_path.is_file())

    def test_alias_maps_onboarding_and_change(self) -> None:
        onb = resolve_scenario_token(_CONFIG, "\u5f00\u6237")
        self.assertIsNotNone(onb)
        self.assertEqual(onb.scenario_id, "corp_onboarding")
        chg = resolve_scenario_token(_CONFIG, "\u53d8\u66f4")
        self.assertEqual(chg.scenario_id, "corp_change")
        dft = resolve_scenario_token(_CONFIG, "default")
        self.assertEqual(dft.scenario_id, "default")

    def test_utterance_maps_single_alias(self) -> None:
        pack = match_from_utterance(_CONFIG, "\u8bf7\u68c0\u67e5\u8fd9\u4efd\u5bf9\u516c\u5f00\u6237\u5c3d\u8c03")
        self.assertIsNotNone(pack)
        self.assertEqual(pack.scenario_id, "corp_onboarding")

    def test_empty_with_proceed_uses_default(self) -> None:
        pack = resolve_pack(_CONFIG, scenario="", proceed_with_gaps=True, hitl_enabled=True)
        self.assertEqual(pack.scenario_id, "default")

    def test_empty_without_proceed_pauses(self) -> None:
        pack = resolve_pack(_CONFIG, scenario="", proceed_with_gaps=False, hitl_enabled=True)
        self.assertIsNone(pack)

    def test_hitl_off_uses_default(self) -> None:
        pack = resolve_pack(_CONFIG, scenario="", proceed_with_gaps=False, hitl_enabled=False)
        self.assertEqual(pack.scenario_id, "default")

    def test_public_catalog_shape(self) -> None:
        rows = public_catalog(_CONFIG)
        self.assertGreaterEqual(len(rows), 3)
        self.assertIn("id", rows[0])
        self.assertIn("aliases", rows[0])


if __name__ == "__main__":
    unittest.main()
