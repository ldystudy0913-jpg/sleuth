"""Graph routing and invoke regression tests."""
from __future__ import annotations

import unittest

from dd_check.config import Settings
from dd_check.graph.build import describe_graph
from dd_check.graph.routing import after_parse, after_score, after_summary, hitl_needed
from dd_check.graph.runner import invoke_check, reset_graphs
from dd_check.models import CheckRequest, FindingStatus
from tests.test_orchestrator import _user_like_payload


class GraphTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_graphs()

    def test_describe_graph(self):
        info = describe_graph(hitl_enabled=False)
        self.assertEqual(info["name"], "dd_analyst_check")
        self.assertTrue(info["nodes"])
        self.assertIn("human_confirm?", info["nodes"])

    def test_routing_attachments(self):
        self.assertEqual(after_parse({"need_attachments": True}), "fetch_attachments")
        self.assertEqual(after_parse({"need_attachments": False}), "skip_attachments")

    def test_routing_hitl_and_llm(self):
        self.assertEqual(
            after_score({"llm_enabled": True, "hitl_enabled": True}),
            "llm_summarize",
        )
        self.assertEqual(
            after_score({"llm_enabled": False, "hitl_enabled": False}),
            "emit_result",
        )
        self.assertEqual(
            after_score({"llm_enabled": False, "hitl_enabled": True}),
            "human_confirm",
        )
        self.assertEqual(
            after_summary({"hitl_enabled": True}),
            "human_confirm",
        )
        self.assertEqual(
            after_summary({"hitl_enabled": False}),
            "emit_result",
        )

    def test_hitl_on_fail_only(self):
        class F:
            status = FindingStatus.WARN

        self.assertFalse(
            hitl_needed(
                {
                    "hitl_enabled": True,
                    "hitl_on_fail_only": True,
                    "findings": [F()],
                }
            )
        )

        class F2:
            status = FindingStatus.FAIL

        self.assertTrue(
            hitl_needed(
                {
                    "hitl_enabled": True,
                    "hitl_on_fail_only": True,
                    "findings": [F2()],
                }
            )
        )

    def test_invoke_user_sample(self):
        req = CheckRequest.model_validate(_user_like_payload())
        result = invoke_check(req, Settings(hitl_enabled=False))
        self.assertEqual(result.reportId, "WSS2606120900003")
        self.assertEqual(result.custType, "PRIVATE")
        statuses = {f.status for f in result.findings}
        self.assertIn(FindingStatus.FAIL, statuses)
        self.assertIn("trace", result.metadata)
        self.assertTrue(result.metadata["trace"])


if __name__ == "__main__":
    unittest.main()
