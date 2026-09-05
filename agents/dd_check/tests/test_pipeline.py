"""Check pipeline: scoring, mock LLM/KB, Word bytes, files[]."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dd_check.config import Settings
from dd_check.kb import reset_token_cache
from dd_check.pipeline import check_report
from dd_check.report_docx import render_docx_bytes
from dd_check.rubric import aggregate_score, load_rubric

_PACK = Path(__file__).resolve().parents[1]


def _settings(**kwargs) -> Settings:
    body = {
        "attachments_enabled": True,
        "kb_enabled": False,
        "output_enabled": False,
        "config_dir": _PACK / "config",
        "rubric_path": _PACK / "config" / "rubric.json",
        "system_prompt_path": _PACK / "config" / "prompts" / "system.md",
        "user_prompt_path": _PACK / "config" / "prompts" / "user.md",
        "llm_base_url": "http://llm.example/v1",
        "llm_api_key": "k",
        "llm_model": "test-model",
    }
    body.update(kwargs)
    return Settings(**body)


def _llm_payload(**overrides):
    body = {
        "dimension_scores": {
            "logic_consistency": 3,
            "completeness": 4,
            "attachment_validity": 4,
        },
        "findings": [
            {
                "dimension": "logic_consistency",
                "severity": "fail",
                "location": "基本信息.姓名",
                "issue": "与附件不一致",
                "evidence": "正文与证件姓名不同",
            }
        ],
        "summary": "存在关键不一致",
        "kb_questions": ["受益所有人识别口径"],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


class _JsonResp:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status
        self.code = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _KbOpener:
    def open(self, req, timeout=None):
        url = getattr(req, "full_url", None) or req.get_full_url()
        if "login" in url or "auth" in url:
            return _JsonResp(
                {
                    "returnCode": "SUC0000",
                    "body": {"ragToken": "tok", "expireTime": 9_999_999_999_999},
                }
            )
        return _JsonResp(
            {
                "returnCode": "SUC0000",
                "body": [
                    {
                        "title": "填写规范",
                        "fileName": "rule.pdf",
                        "dmzUrl": "https://kb.example/rule",
                        "rankScore": 0.9,
                    }
                ],
            }
        )


class RubricScoreTests(unittest.TestCase):
    def test_two_equal_weights_round_one_decimal(self):
        rubric = {
            "score": {"max": 5, "decimals": 1},
            "word": {"filename": "x_{score}_{date}.docx", "date_format": "%Y%m%d"},
            "dimensions": [
                {"id": "a", "weight": 1.0},
                {"id": "b", "weight": 1.0},
            ],
        }
        self.assertEqual(aggregate_score({"a": 3, "b": 4}, rubric), 3.5)

    def test_load_pack_rubric(self):
        data = load_rubric(_PACK / "config" / "rubric.json")
        self.assertEqual(data["score"]["max"], 5)
        self.assertGreaterEqual(len(data["dimensions"]), 3)


class PipelineTests(unittest.TestCase):
    def test_missing_llm_returns_error(self):
        s = _settings(llm_base_url="", llm_api_key="", llm_model="")
        body = check_report(s, report_text="hello")
        self.assertFalse(body.get("ok"))
        self.assertIn("LLM", body.get("detail") or "")
        self.assertEqual(body.get("sources"), [])
        self.assertEqual(body.get("files"), [])

    def test_empty_env_uses_sleuth_llm_json(self):
        captured = {}

        def fake_llm(messages, settings):
            captured["model"] = settings.llm_model
            captured["key"] = settings.llm_api_key
            return _llm_payload()

        body = check_report(
            _settings(llm_base_url="", llm_api_key="", llm_model=""),
            report_text="客户张三",
            sleuth_llm_json=json.dumps(
                {
                    "model": "sess-model",
                    "base_url": "https://sess.example/v1",
                    "api_key": "sk-sess",
                }
            ),
            llm_fn=fake_llm,
            emit_fn=lambda settings, **kwargs: {"ok": True, "files": []},
        )
        self.assertTrue(body.get("ok"))
        self.assertEqual(captured.get("model"), "sess-model")
        self.assertEqual(captured.get("key"), "sk-sess")

    def test_agent_llm_env_wins_over_sleuth_json(self):
        captured = {}

        def fake_llm(messages, settings):
            captured["model"] = settings.llm_model
            return _llm_payload()

        body = check_report(
            _settings(),
            report_text="客户张三",
            sleuth_llm_json=json.dumps(
                {
                    "model": "sess-model",
                    "base_url": "https://sess.example/v1",
                    "api_key": "sk-sess",
                }
            ),
            llm_fn=fake_llm,
            emit_fn=lambda settings, **kwargs: {"ok": True, "files": []},
        )
        self.assertTrue(body.get("ok"))
        self.assertEqual(captured.get("model"), "test-model")

    def test_check_without_kb_has_findings_and_files(self):
        captured = {}

        def fake_llm(messages, settings):
            captured["n"] = captured.get("n", 0) + 1
            return _llm_payload()

        def fake_emit(settings, **kwargs):
            data = kwargs.get("content_bytes") or b""
            return {
                "ok": True,
                "files": [
                    {
                        "filename": kwargs.get("filename"),
                        "mime": kwargs.get("mime"),
                        "size": len(data),
                        "url": "https://example.com/r.docx",
                    }
                ],
            }

        body = check_report(
            _settings(),
            report_text="客户张三",
            report_json='{"name":"张三"}',
            attachment_refs=[{"filename": "id.pdf", "excerpt": "姓名李四"}],
            llm_fn=fake_llm,
            emit_fn=fake_emit,
        )
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("score"), 3.7)
        self.assertTrue(body.get("findings"))
        self.assertEqual(body["findings"][0]["location"], "基本信息.姓名")
        self.assertEqual(body.get("sources"), [])
        self.assertTrue(body.get("files"))
        self.assertEqual(body["files"][0]["url"], "https://example.com/r.docx")
        self.assertEqual(captured.get("n"), 1)

    def test_kb_mock_fills_sources(self):
        reset_token_cache()

        def fake_llm(messages, settings):
            return _llm_payload()

        def fake_emit(settings, **kwargs):
            return {"ok": True, "files": [{"filename": "x.docx", "url": "https://example.com/x.docx"}]}

        s = _settings(
            kb_enabled=True,
            kb_api_url="http://kb.example/search",
            kb_login_url="http://kb.example/login",
            kb_openid="oid",
            kb_service_id="sid",
            kb_max_queries=2,
        )
        body = check_report(
            s,
            report_text="报告",
            llm_fn=fake_llm,
            kb_opener=_KbOpener(),
            emit_fn=fake_emit,
        )
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("sources"))
        self.assertEqual(body["sources"][0]["url"], "https://kb.example/rule")


class DocxTests(unittest.TestCase):
    def test_docx_bytes_are_zip(self):
        try:
            import docx  # noqa: F401
        except ImportError:
            self.skipTest("python-docx not installed")
        rubric = load_rubric(_PACK / "config" / "rubric.json")
        data = render_docx_bytes(
            rubric=rubric,
            score=3.5,
            summary="测试",
            findings=[
                {
                    "dimension": "logic_consistency",
                    "severity": "fail",
                    "location": "A.1",
                    "issue": "矛盾",
                    "evidence": "摘录",
                }
            ],
            sources=[{"title": "规范", "url": "https://kb.example/a"}],
        )
        self.assertTrue(data.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
