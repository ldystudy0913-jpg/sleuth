"""Strategy resolver tests."""
from __future__ import annotations

import unittest

from dd_check.config import Settings
from dd_check.models import CustType, Phase
from dd_check.strategy import StrategyResolver


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.resolver = StrategyResolver(Settings())

    def test_cust_type_alias_p(self):
        self.assertEqual(self.resolver.normalize_cust_type("p"), CustType.PRIVATE)

    def test_default_check_dimensions(self):
        s = self.resolver.resolve("vsdbvsb", CustType.PRIVATE, Phase.CHECK)
        self.assertIn("id_validity", s.enabled_for(CustType.PRIVATE))
        self.assertNotIn("beneficial_owner", s.enabled_for(CustType.PRIVATE))
        self.assertIn("beneficial_owner", s.enabled_for(CustType.CORPORATE))

    def test_list_strategy(self):
        s = self.resolver.resolve("名单", CustType.PRIVATE, Phase.CHECK)
        dims = s.enabled_for(CustType.PRIVATE)
        self.assertIn("attachment_sanction_geo", dims)
        self.assertNotIn("basic_info_completeness", dims)

    def test_recheck_has_approval(self):
        s = self.resolver.resolve("any", CustType.PRIVATE, Phase.RECHECK)
        self.assertIn("approval_compliance", s.enabled_for(CustType.PRIVATE))


if __name__ == "__main__":
    unittest.main()
