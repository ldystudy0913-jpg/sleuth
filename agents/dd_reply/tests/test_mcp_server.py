"""MCP tool registration tests (no live HTTP)."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from dd_reply.config import Settings
from dd_reply.kb.remote import KbHit, RiskRetrieval
from dd_reply.mcp_server import build_mcp_server


def _ready_settings(**kwargs):
    data = {
        "kb_api_url": "http://kb.test/search",
        "kb_login_url": "http://kb.test/login",
        "kb_login_openid": "oid",
        "kb_login_service_id": "sid",
    }
    data.update(kwargs)
    return Settings(**data)


def _hit() -> KbHit:
    return KbHit.from_dict(
        {
            "id": "1",
            "title": "受益所有人识别",
            "paragraph": "C001 请核实受益所有人。",
            "fileName": "风险点手册.pdf",
            "fileUrl": "https://kb.example/f.pdf",
            "knowledgeId": "10752",
            "rankScore": 0.8,
        }
    )


class TestMcpServer(unittest.TestCase):
    def test_tools_registered(self) -> None:
        settings = _ready_settings()
        server = build_mcp_server(settings)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        for name in (
            "get_agent_card",
            "generate_reply_framework",
            "lookup_risk_kb",
            "list_risk_codes",
            "list_lexicon",
            "health",
        ):
            self.assertIn(name, tools)

        health = json.loads(tools["health"].fn())
        self.assertTrue(health.get("ok"))
        self.assertTrue(health.get("kb_api_configured"))
        self.assertGreaterEqual(health.get("lexicon_rule_count", 0), 8)
        self.assertNotIn("kb_fallback_local", health)

        need = json.loads(
            tools["generate_reply_framework"].fn(risk_codes_json='["C001"]')
        )
        self.assertEqual(need.get("status"), "need_input")
        self.assertIn("客户名称", need.get("missing") or [])

        codes = json.loads(tools["list_risk_codes"].fn())
        self.assertEqual(codes.get("codes"), [])
        self.assertEqual(codes.get("source"), "remote_api")

        def fake_retrieve(codes, settings, **kwargs):
            out = []
            for c in codes:
                cu = str(c).upper()
                if cu == "C001":
                    out.append(RiskRetrieval(code=cu, question=cu, hits=[_hit()]))
                else:
                    out.append(
                        RiskRetrieval(code=cu, question=cu, hits=[], error="empty_hits")
                    )
            return out

        with patch("dd_reply.mcp_server.retrieve_risk_codes", side_effect=fake_retrieve):
            looked = json.loads(tools["lookup_risk_kb"].fn(codes_json='["C001","C999"]'))
        self.assertEqual(len(looked.get("found") or []), 1)
        self.assertEqual(looked["found"][0]["code"], "C001")
        self.assertTrue(looked["found"][0]["sources"])
        self.assertTrue(looked.get("sources"))
        self.assertEqual(looked["sources"][0]["title"], "风险点手册.pdf")
        self.assertEqual(looked["sources"][0]["url"], "https://kb.example/f.pdf")
        self.assertEqual(len(looked.get("missing") or []), 1)

        card = json.loads(tools["get_agent_card"].fn())
        self.assertEqual(card.get("name"), "dd_reply")
        self.assertEqual(card.get("title"), "尽调答复框架生成助手")
        self.assertTrue(card.get("skills"))

        def fake_pipeline_retrieve(codes, settings, **kwargs):
            return [
                RiskRetrieval(code=str(c).upper(), question=str(c).upper(), hits=[_hit()])
                for c in codes
            ]

        with patch(
            "dd_reply.pipeline.retrieve_risk_codes",
            side_effect=fake_pipeline_retrieve,
        ):
            out = json.loads(
                tools["generate_reply_framework"].fn(
                    risk_codes_json='["C005"]',
                    customer_name="乙公司",
                    proceed_with_gaps=True,
                )
            )
        self.assertIn("markdown", out)
        self.assertIn("待核实", out["markdown"])
        self.assertIn("知识来源", out["markdown"])
        self.assertIn("---", out["markdown"])
        self.assertIn('style="color:#888"', out["markdown"])
        self.assertNotIn("## 知识来源", out["markdown"])
        self.assertIn("https://kb.example/f.pdf", out["markdown"])
        self.assertTrue(out.get("sources"))
        self.assertEqual(out["sources"][0]["url"], "https://kb.example/f.pdf")

        missing_url = build_mcp_server(Settings(kb_api_url=""))
        tools2 = {t.name: t for t in missing_url._tool_manager.list_tools()}
        err = json.loads(
            tools2["generate_reply_framework"].fn(
                risk_codes_json='["C001"]',
                proceed_with_gaps=True,
            )
        )
        self.assertIn("DD_REPLY_KB_API_URL", err.get("error") or "")

    def test_http_health_route(self) -> None:
        settings = _ready_settings()
        server = build_mcp_server(settings)
        routes = getattr(server, "_custom_starlette_routes", None) or []
        paths = [getattr(r, "path", "") for r in routes]
        self.assertIn("/health", paths)

        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        app = server.streamable_http_app()
        with TestClient(app) as client:
            r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("service"), "dd-reply-tools")
        self.assertTrue(body.get("kb_api_configured"))


if __name__ == "__main__":
    unittest.main()
