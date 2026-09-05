"""BizError catalog, APPError, and HTTP envelope."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sleuth.bizerror import APPError, BizErrorCode, ok_payload
from sleuth.server.app import create_app
from sleuth.server.envelope import json_ok


class CatalogTests(unittest.TestCase):
    def test_success_envelope(self):
        body = ok_payload({"ok": True})
        self.assertEqual(body["code"], BizErrorCode.SUC0000.code)
        self.assertEqual(body["msg"], BizErrorCode.SUC0000.error_message)
        self.assertEqual(body["data"], {"ok": True})

    def test_app_error_of_and_format(self):
        err = APPError.of(BizErrorCode.SESSION_NOT_FOUND, "sess_1", status=404)
        self.assertEqual(err.code, "AMLS001")
        self.assertIn("sess_1", err.msg)
        self.assertEqual(err.status, 404)
        env = err.envelope()
        self.assertEqual(env["code"], "AMLS001")
        self.assertIsNone(env["data"])

    def test_amls_file_not_ready(self):
        self.assertEqual(BizErrorCode.FILE_NOT_READY.code, "AMLS002")
        msg = BizErrorCode.FILE_NOT_READY.format_message("file_x")
        self.assertIn("file_x", msg)


class HttpEnvelopeTests(unittest.TestCase):
    def test_health_and_limits_wrapped(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(workdir=Path(td))
            from starlette.testclient import TestClient

            client = TestClient(app)
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            body = health.json()
            self.assertEqual(body["code"], BizErrorCode.SUC0000.code)
            self.assertEqual(body["data"]["ok"], True)

            limits = client.get("/v1/files/limits")
            self.assertEqual(limits.status_code, 200)
            data = limits.json()["data"]
            self.assertIn("max_bytes", data)

    def test_session_not_found_uses_amls001(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(workdir=Path(td))
            from starlette.testclient import TestClient

            client = TestClient(app)
            res = client.get(
                "/v1/sessions/sess_missing000000000001",
                headers={"X-User-Id": "alice"},
            )
            self.assertEqual(res.status_code, 404)
            body = res.json()
            self.assertEqual(body["code"], BizErrorCode.SESSION_NOT_FOUND.code)
            self.assertIn("sess_missing000000000001", body["msg"])


class JsonOkTests(unittest.TestCase):
    def test_json_ok_status(self):
        resp = json_ok({"n": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body.decode("utf-8").count("SUC0000"), 1)


if __name__ == "__main__":
    unittest.main()
