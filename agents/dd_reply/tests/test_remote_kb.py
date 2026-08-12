"""Remote KB client + pipeline remote mode tests."""
from __future__ import annotations

import json
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock

import httpx

from dd_reply.config import Settings
from dd_reply.kb.remote import KbApiError, KbHit, search_knowledge
from dd_reply.models import FrameworkRequest
from dd_reply.pipeline import generate_framework


def _sample_body() -> Dict[str, Any]:
    return {
        "returnCode": "SUC0000",
        "errorMsg": None,
        "body": [
            {
                "id": "1",
                "title": "行政处罚记录",
                "paragraph": "C011 对应尽调问题：请核实行政处罚事由与整改。判断要点：①核对决定书 ②整改证明 ③结论。对应材料：处罚决定书、整改证明。",
                "fileName": "风险点手册.pdf",
                "rankScore": 0.9,
                "comprehended": 1,
                "finalResponse": 1,
                "splitContents": [
                    {
                        "type": "text",
                        "content": "C011 行政处罚",
                        "id": "a",
                        "url": "",
                    }
                ],
            }
        ],
    }


class TestRemoteKbClient(unittest.TestCase):
    def test_search_parses_hits(self) -> None:
        settings = Settings(
            kb_api_url="http://kb.test/search",
            kb_api_token="tok",
            kb_knowledge_id="10752",
        )
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=_sample_body())
        )
        with httpx.Client(transport=transport) as client:
            hits = search_knowledge("C011", settings, client=client)
        self.assertEqual(len(hits), 1)
        self.assertIsInstance(hits[0], KbHit)
        self.assertIn("行政处罚", hits[0].paragraph)
        self.assertEqual(hits[0].final_response, 1)

    def test_search_rejects_bad_return_code(self) -> None:
        settings = Settings(kb_api_url="http://kb.test/search")
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"returnCode": "ERR", "errorMsg": "boom", "body": []}
            )
        )
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(KbApiError):
                search_knowledge("C011", settings, client=client)

    def test_auth_and_extra_body_sent(self) -> None:
        captured: List[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=_sample_body())

        settings = Settings(
            kb_api_url="http://kb.test/search",
            kb_api_token="secret",
            kb_api_extra_body='{"topK": 5}',
            kb_knowledge_id="10752",
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            search_knowledge("C011", settings, client=client)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].headers.get("Authorization"), "Bearer secret")
        payload = json.loads(captured[0].content.decode("utf-8"))
        self.assertEqual(payload["question"], "C011")
        self.assertEqual(payload["topK"], 5)
        self.assertEqual(payload["knowledgeId"], "10752")


class TestPipelineRemote(unittest.TestCase):
    def test_remote_mode_uses_hits(self) -> None:
        settings = Settings(
            kb_api_url="http://kb.test/search",
            kb_api_token="t",
            kb_fallback_local=False,
        )
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=_sample_body())
        )

        # Patch retrieve path via httpx default is hard; monkeypatch retrieve_risk_codes
        from dd_reply import pipeline as pl

        def fake_retrieve(codes, settings, **kwargs):
            from dd_reply.kb.remote import RiskRetrieval

            hits = [KbHit.from_dict(_sample_body()["body"][0])]
            return [
                RiskRetrieval(code=c.upper(), question=c.upper(), hits=hits)
                for c in codes
            ]

        old = pl.retrieve_risk_codes
        pl.retrieve_risk_codes = fake_retrieve  # type: ignore[assignment]
        try:
            req = FrameworkRequest(
                risk_codes=["C011"],
                customer_name="某某公司",
            )
            result = generate_framework(req, settings=settings, use_llm=False)
            self.assertEqual(result.meta.get("kb", {}).get("mode"), "remote")
            self.assertIn("C011", result.meta.get("found_codes", []))
            self.assertIn("C011", result.markdown)
            self.assertIn("充分", result.markdown)
        finally:
            pl.retrieve_risk_codes = old  # type: ignore[assignment]

    def test_remote_empty_without_fallback_is_missing(self) -> None:
        from dd_reply import pipeline as pl
        from dd_reply.kb.remote import RiskRetrieval

        settings = Settings(
            kb_api_url="http://kb.test/search",
            kb_fallback_local=False,
        )

        def fake_retrieve(codes, settings, **kwargs):
            return [
                RiskRetrieval(code=c.upper(), question=c.upper(), hits=[], error="empty")
                for c in codes
            ]

        old = pl.retrieve_risk_codes
        pl.retrieve_risk_codes = fake_retrieve  # type: ignore[assignment]
        try:
            result = generate_framework(
                FrameworkRequest(risk_codes=["C099"]),
                settings=settings,
                use_llm=False,
            )
            self.assertEqual(result.meta.get("missing_codes"), ["C099"])
            self.assertEqual(result.meta.get("found_codes"), [])
        finally:
            pl.retrieve_risk_codes = old  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
