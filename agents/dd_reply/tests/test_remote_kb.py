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
                "fileUrl": "https://kb.example/files/risk-manual.pdf",
                "knowledgeId": "10752",
                "sourceName": "尽调知识库",
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
        cite = hits[0].source_cite()
        self.assertIn("风险点手册.pdf", cite)
        self.assertIn("https://kb.example/files/risk-manual.pdf", cite)
        prompt = hits[0].text_for_prompt()
        self.assertIn("来源:", prompt)
        self.assertIn("风险点手册.pdf", prompt)

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
            kb_api_extra_body='{"foo": 1}',
            kb_knowledge_id="10752",
            kb_top_k=5,
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            search_knowledge("C011", settings, client=client)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].headers.get("Authorization"), "Bearer secret")
        payload = json.loads(captured[0].content.decode("utf-8"))
        self.assertEqual(payload["question"], "C011")
        self.assertEqual(payload["foo"], 1)
        self.assertEqual(payload["topK"], 5)
        self.assertEqual(payload["knowledgeId"], "10752")

    def test_top_k_slices_hits(self) -> None:
        body = {
            "returnCode": "SUC0000",
            "body": [
                {
                    "id": str(i),
                    "title": f"t{i}",
                    "paragraph": f"p{i}",
                    "fileName": f"f{i}.pdf",
                    "rankScore": float(i),
                    "comprehended": 0,
                    "finalResponse": 0,
                }
                for i in range(12)
            ],
        }
        settings = Settings(kb_api_url="http://kb.test/search", kb_top_k=3)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
        with httpx.Client(transport=transport) as client:
            hits = search_knowledge("C011", settings, client=client)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0].id, "11")

    def test_name_query_not_uppercased(self) -> None:
        from dd_reply.models import normalize_risk_query

        self.assertEqual(normalize_risk_query("c011"), "C011")
        self.assertEqual(normalize_risk_query("行政处罚记录"), "行政处罚记录")


class TestPipelineRemote(unittest.TestCase):
    def test_remote_mode_uses_hits(self) -> None:
        settings = Settings(
            kb_api_url="http://kb.test/search",
            kb_api_token="t",
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
            self.assertIn("知识来源", result.markdown)
            self.assertIn("风险点手册.pdf", result.markdown)
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
