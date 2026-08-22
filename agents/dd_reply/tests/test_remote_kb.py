"""Remote KB client + pipeline remote mode tests."""
from __future__ import annotations

import json
import time
import unittest
from typing import Any, Dict, List

import httpx

from dd_reply.config import Settings
from dd_reply.kb.remote import KbApiError, KbHit, reset_token_cache, search_knowledge
from dd_reply.models import FrameworkRequest
from dd_reply.pipeline import generate_framework


def _ready_settings(**kwargs: Any) -> Settings:
    data: Dict[str, Any] = {
        "kb_api_url": "http://kb.test/search",
        "kb_login_url": "http://kb.test/login",
        "kb_login_openid": "oid",
        "kb_login_service_id": "sid",
    }
    data.update(kwargs)
    return Settings(**data)


def _login_body() -> Dict[str, Any]:
    return {
        "returnCode": "SUC0000",
        "body": {
            "ragToken": "rag-test",
            "expireTime": int(time.time()) + 3600,
        },
    }


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


def _handler(search_json: Dict[str, Any]):
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "login" in url:
            return httpx.Response(200, json=_login_body())
        return httpx.Response(200, json=search_json)

    return handler


class TestRemoteKbClient(unittest.TestCase):
    def setUp(self) -> None:
        reset_token_cache()

    def test_search_parses_hits(self) -> None:
        settings = _ready_settings()
        with httpx.Client(transport=httpx.MockTransport(_handler(_sample_body()))) as client:
            hits = search_knowledge("C011", settings, client=client)
        self.assertEqual(len(hits), 1)
        self.assertIsInstance(hits[0], KbHit)
        self.assertIn("行政处罚", hits[0].paragraph)
        self.assertEqual(hits[0].final_response, 1)
        self.assertEqual(hits[0].source_url(), "https://kb.example/files/risk-manual.pdf")
        cite = hits[0].source_cite()
        self.assertIn("风险点手册.pdf", cite)
        self.assertIn("https://kb.example/files/risk-manual.pdf", cite)
        prompt = hits[0].text_for_prompt()
        self.assertIn("来源:", prompt)
        self.assertIn("风险点手册.pdf", prompt)

    def test_source_url_prefers_dmz(self) -> None:
        hit = KbHit.from_dict(
            {
                "dmzUrl": "https://dmz.example/a.pdf",
                "fileUrl": "https://inner.example/a.pdf",
                "fileName": "a.pdf",
            }
        )
        self.assertEqual(hit.source_url(), "https://dmz.example/a.pdf")

    def test_search_rejects_bad_return_code(self) -> None:
        settings = _ready_settings()
        transport = httpx.MockTransport(
            _handler({"returnCode": "ERR", "errorMsg": "boom", "body": []})
        )
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(KbApiError):
                search_knowledge("C011", settings, client=client)

    def test_cookie_and_service_config_sent(self) -> None:
        captured: List[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            if "login" in str(req.url):
                return httpx.Response(200, json=_login_body())
            return httpx.Response(200, json=_sample_body())

        settings = _ready_settings(
            kb_knowledge_ids="10752",
            kb_sort_count=5,
            kb_sort_score=0.0,
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            search_knowledge("C011", settings, client=client)
        self.assertEqual(len(captured), 2)
        login_req, search_req = captured
        login_payload = json.loads(login_req.content.decode("utf-8"))
        self.assertEqual(login_payload["openId"], "oid")
        self.assertEqual(login_payload["serviceId"], "sid")
        self.assertEqual(search_req.headers.get("cookie"), "ragToken=rag-test")
        self.assertIsNone(search_req.headers.get("authorization"))
        payload = json.loads(search_req.content.decode("utf-8"))
        self.assertEqual(payload["question"], "C011")
        self.assertNotIn("topK", payload)
        self.assertNotIn("knowledgeId", payload)
        sc = payload["serviceConfig"]
        self.assertEqual(sc["sortConfig"]["sortCount"], 5)
        self.assertEqual(sc["sortConfig"]["sortScore"], 0.0)
        self.assertEqual(sc["recallConfig"][0]["knowledgeId"], "10752")

    def test_sort_count_slices_hits(self) -> None:
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
        settings = _ready_settings(kb_sort_count=3)
        with httpx.Client(transport=httpx.MockTransport(_handler(body))) as client:
            hits = search_knowledge("C011", settings, client=client)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0].id, "11")

    def test_name_query_not_uppercased(self) -> None:
        from dd_reply.models import normalize_risk_query

        self.assertEqual(normalize_risk_query("c011"), "C011")
        self.assertEqual(normalize_risk_query("行政处罚记录"), "行政处罚记录")


class TestPipelineRemote(unittest.TestCase):
    def test_remote_mode_uses_hits(self) -> None:
        settings = _ready_settings()

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
            self.assertIn("---", result.markdown)
            self.assertIn('style="color:#888"', result.markdown)
            self.assertNotIn("## 知识来源", result.markdown)
            self.assertIn("https://kb.example/files/risk-manual.pdf", result.markdown)
            self.assertEqual(result.meta.get("kb", {}).get("mode"), "remote")
            self.assertIn("C011", result.meta.get("found_codes", []))
            self.assertIn("C011", result.markdown)
            self.assertIn("充分", result.markdown)
        finally:
            pl.retrieve_risk_codes = old  # type: ignore[assignment]

    def test_remote_empty_without_fallback_is_missing(self) -> None:
        from dd_reply import pipeline as pl
        from dd_reply.kb.remote import RiskRetrieval

        settings = _ready_settings()

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
