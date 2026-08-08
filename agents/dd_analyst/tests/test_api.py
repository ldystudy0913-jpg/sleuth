"""HTTP API smoke tests with Starlette TestClient."""
from __future__ import annotations

import json
import unittest

from starlette.testclient import TestClient

from dd_check.api import create_app
from dd_check.config import Settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(Settings()))

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_check_endpoint(self):
        body = {
            "reportId": "R1",
            "investId": "",
            "result": json.dumps(
                [
                    {
                        "label": "客户基本信息",
                        "code": "custInfo",
                        "type": "tabled-input",
                        "value": [{"客户名称": "张三"}, {"客户号": "1"}],
                    }
                ],
                ensure_ascii=False,
            ),
            "question": "检查",
            "busCode": "x",
            "custType": "p",
            "phase": "CHECK",
            "currentDateTime": "2026-07-09 06:36:46",
        }
        r = self.client.post("/v1/check", json=body)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("score", data)
        self.assertIn("resultId", data)
        rid = data["resultId"]
        g = self.client.get(f"/v1/results/{rid}")
        self.assertEqual(g.status_code, 200)


if __name__ == "__main__":
    unittest.main()
