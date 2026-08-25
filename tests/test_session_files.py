"""Session COS mailbox, HTTP file routes, default-agent kb/save tools."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sleuth.config import Config, _apply_env
from sleuth.files.cos import CosNotConfigured, MemoryObjectStore
from sleuth.files.ingest import wait_extracts
from sleuth.files.mailbox import (
    MailboxError,
    complete_upload,
    create_upload,
    harvest_tool_files,
    object_key,
    public_files,
    put_generated_text,
)
from sleuth.kb import reset_token_cache, search_knowledge
from sleuth.permission import Permission, Rule, build_rules, evaluate
from sleuth.storage.base import SessionRecord
from sleuth.storage.sqlite import SQLiteStore
from sleuth.tools.base import ToolContext
from sleuth.tools.kb_lookup import KbLookupTool
from sleuth.tools.registry import ToolRegistry
from sleuth.tools.save_output_file import SaveOutputFileTool
from sleuth.util.ids import file_id


def _cfg() -> Config:
    cfg = Config()
    cfg.cos.secret_id = "sid"
    cfg.cos.secret_key = "skey"
    cfg.cos.region = "ap-guangzhou"
    cfg.cos.bucket = "demo-bucket"
    cfg.cos.endpoint = "https://cos.ap-guangzhou.myqcloud.com"
    cfg.cos.path_prefix = "mailbox"
    cfg.files.max_bytes = 1024
    cfg.files.max_count = 4
    cfg.kb.api_url = "http://kb.test/search"
    cfg.kb.login_url = "http://kb.test/login"
    cfg.kb.openid = "oid"
    cfg.kb.service_id = "sid"
    return cfg


def _rec(store, sid="sess_filetest000000000001"):
    rec = SessionRecord(
        id=sid,
        directory="/tmp",
        title="t",
        user_id="alice",
        agent="build",
        metadata={},
    )
    store.create_session(rec)
    return store.get_session(sid)


class FileIdTests(unittest.TestCase):
    def test_prefix(self):
        self.assertTrue(file_id().startswith("file_"))
        self.assertNotEqual(file_id(), file_id())


class EnvCosKbTests(unittest.TestCase):
    def test_apply_env_cos_files_kb(self):
        old = dict(os.environ)
        try:
            os.environ["AWS_ACCESS_KEY_ID"] = "id1"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "key1"
            os.environ["AWS_DEFAULT_REGION"] = "shenzhen"
            os.environ["SLEUTH_S3_ENDPOINT"] = "http://cos.example.internal"
            os.environ["SLEUTH_SKILLS_S3"] = json.dumps(
                [{"uri": "s3://b1/sleuth/skills/"}]
            )
            os.environ["SLEUTH_COS_PATH_PREFIX"] = "sleuth/files"
            os.environ["SLEUTH_FILES_MAX_BYTES"] = "2048"
            os.environ["SLEUTH_FILES_MIME_ALLOW"] = "text/plain,application/pdf"
            os.environ["SLEUTH_KB_API_URL"] = "http://kb.example/s"
            os.environ["SLEUTH_KB_LOGIN_URL"] = "http://kb.example/login"
            os.environ["SLEUTH_KB_OPENID"] = "oid"
            os.environ["SLEUTH_KB_SERVICEID"] = "sid"
            os.environ["SLEUTH_KB_KNOWLEDGE_IDS"] = "10752"
            os.environ["SLEUTH_KB_SORT_COUNT"] = "3"
            os.environ["SLEUTH_KB_SORT_SCORE"] = "0.0"
            cfg = Config()
            _apply_env(cfg)
            self.assertEqual(cfg.cos.secret_id, "id1")
            self.assertEqual(cfg.cos.secret_key, "key1")
            self.assertEqual(cfg.cos.region, "shenzhen")
            self.assertEqual(cfg.cos.endpoint, "http://cos.example.internal")
            self.assertEqual(cfg.cos.bucket, "b1")
            self.assertEqual(cfg.cos.path_prefix, "sleuth/files")
            self.assertTrue(cfg.cos.configured())
            self.assertEqual(cfg.files.max_bytes, 2048)
            self.assertEqual(cfg.files.mime_allow, ["text/plain", "application/pdf"])
            self.assertEqual(cfg.kb.api_url, "http://kb.example/s")
            self.assertEqual(cfg.kb.login_url, "http://kb.example/login")
            self.assertEqual(cfg.kb.openid, "oid")
            self.assertEqual(cfg.kb.service_id, "sid")
            self.assertEqual(cfg.kb.knowledge_ids, "10752")
            self.assertEqual(cfg.kb.sort_count, 3)
            self.assertEqual(cfg.kb.sort_score, 0.0)
            self.assertTrue(cfg.kb.configured())
        finally:
            os.environ.clear()
            os.environ.update(old)


class MailboxTests(unittest.TestCase):
    def test_nested_path_prefix_inserts_folder_before_user(self):
        cfg = _cfg()
        cfg.cos.path_prefix = "sleuth/files"
        key = object_key(
            config=cfg,
            user_id="alice",
            session_id="sess_abc",
            file_id="file_xyz",
            filename="notes.txt",
        )
        self.assertEqual(key, "sleuth/files/alice/sess_abc/file_xyz/notes.txt")
        self.assertFalse(key.startswith("sleuth/alice/"))

    def test_upload_complete_and_oversize(self):
        cfg = _cfg()
        mem = MemoryObjectStore()
        with tempfile.TemporaryDirectory() as td:
            store = SQLiteStore(Path(td) / "t.db")
            rec = _rec(store)
            with self.assertRaises(MailboxError) as ctx:
                create_upload(
                    config=cfg,
                    store=store,
                    rec=rec,
                    filename="big.txt",
                    mime="text/plain",
                    size=5000,
                    object_store=mem,
                )
            self.assertEqual(ctx.exception.status, 413)

            rec = store.get_session(rec.id)
            payload = create_upload(
                config=cfg,
                store=store,
                rec=rec,
                filename="notes.txt",
                mime="text/plain",
                size=5,
                object_store=mem,
            )
            self.assertIn("upload_url", payload)
            self.assertTrue(payload["object_key"].startswith("mailbox/"))
            mem.put_bytes(key=payload["object_key"], data=b"hello", mime="text/plain")
            rec = store.get_session(rec.id)
            item = complete_upload(
                config=cfg,
                store=store,
                rec=rec,
                file_id=payload["file_id"],
                object_store=mem,
            )
            self.assertEqual(item["status"], "ready")
            self.assertEqual(item["size"], 5)
            self.assertEqual(item.get("excerpt_status"), "pending")
            wait_extracts(3.0)
            rec = store.get_session(rec.id)
            listed = public_files(rec)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["id"], payload["file_id"])

    def test_put_generated_text_and_harvest(self):
        cfg = _cfg()
        mem = MemoryObjectStore()

        class Sess:
            config = cfg
            id = "sess_gen"
            user_id = "alice"
            store = None
            _files = []
            _turn_file_ids = []

        sess = Sess()
        item = put_generated_text(
            session=sess,
            filename="out.md",
            content="# hi",
            mime="text/markdown",
            object_store=mem,
        )
        self.assertEqual(item["role"], "assistant")
        self.assertIn(item["id"], sess._turn_file_ids)
        atts = harvest_tool_files(
            sess,
            {
                "markdown": "x",
                "files": [
                    {
                        "filename": "doc.txt",
                        "mime": "text/plain",
                        "object_key": "mailbox/alice/sess_gen/x/doc.txt",
                        "size": 1,
                        "url": "https://cos.example/doc.txt",
                    }
                ],
            },
        )
        self.assertEqual(len(atts), 1)
        self.assertTrue(atts[0]["url"].startswith("https://"))


class PermissionBuildTests(unittest.TestCase):
    def test_default_agent_allows_kb_and_mailbox_and_ddreply(self):
        rules = build_rules()
        self.assertEqual(evaluate("kb_lookup", "*", rules).action, "allow")
        self.assertEqual(evaluate("save_output_file", "*", rules).action, "allow")
        self.assertEqual(evaluate("read_session_file", "*", rules).action, "allow")
        self.assertEqual(
            evaluate("ddreply_lookup_risk_kb", "*", rules).action, "allow"
        )


class BuiltinToolTests(unittest.TestCase):
    def test_registry_includes_mailbox_tools(self):
        names = ToolRegistry().names()
        self.assertIn("kb_lookup", names)
        self.assertIn("save_output_file", names)
        self.assertIn("read_session_file", names)

    def test_kb_lookup_and_save_output_file(self):
        cfg = _cfg()
        mem = MemoryObjectStore()

        class Sess:
            config = cfg
            id = "sess_tools"
            user_id = "alice"
            store = None
            _files = []
            _turn_file_ids = []

        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        hits = [
            {
                "title": "受益所有人",
                "file_name": "手册.pdf",
                "url": "https://kb.example/f.pdf",
                "paragraph": "请核实 UBO。",
                "rank_score": 0.9,
                "knowledge_id": "1",
            }
        ]
        with patch("sleuth.tools.kb_lookup.search_knowledge", return_value=hits):
            result = KbLookupTool().execute({"question": "C001"}, ctx)
        self.assertFalse(result.is_error)
        payload = json.loads(result.output)
        self.assertEqual(payload["found"][0]["question"], "C001")

        with patch(
            "sleuth.files.mailbox.object_store_from_config",
            return_value=mem,
        ):
            saved = SaveOutputFileTool().execute(
                {"filename": "a.txt", "content": "hello"},
                ctx,
            )
        self.assertFalse(saved.is_error)
        body = json.loads(saved.output)
        self.assertTrue(body["file_id"].startswith("file_"))
        self.assertIn("download_url", body)


class HttpFileRouteTests(unittest.TestCase):
    def test_uploads_complete_list_download_and_413(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        cfg = _cfg()
        mem = MemoryObjectStore()
        with tempfile.TemporaryDirectory() as td:
            store = SQLiteStore(Path(td) / "t.db")
            rec = _rec(store, sid="sess_httptest000000000001")
            from sleuth.server.app import create_app

            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ), patch(
                "sleuth.files.mailbox.object_store_from_config", return_value=mem
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                headers = {"X-User-Id": "alice"}
                too_big = client.post(
                    f"/v1/sessions/{rec.id}/files/uploads",
                    headers=headers,
                    json={"filename": "big.bin", "mime": "application/octet-stream", "size": 99999},
                )
                self.assertEqual(too_big.status_code, 413)

                up = client.post(
                    f"/v1/sessions/{rec.id}/files/uploads",
                    headers=headers,
                    json={"filename": "notes.txt", "mime": "text/plain", "size": 5},
                )
                self.assertEqual(up.status_code, 200, up.text)
                body = up.json()
                mem.put_bytes(key=body["object_key"], data=b"hello", mime="text/plain")
                done = client.post(
                    f"/v1/sessions/{rec.id}/files/complete",
                    headers=headers,
                    json={"file_id": body["file_id"]},
                )
                self.assertEqual(done.status_code, 200, done.text)
                self.assertEqual(done.json()["status"], "ready")
                wait_extracts(3.0)

                listed = client.get(f"/v1/sessions/{rec.id}/files", headers=headers)
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(len(listed.json()["files"]), 1)

                dl = client.get(
                    f"/v1/sessions/{rec.id}/files/{body['file_id']}?json=1",
                    headers=headers,
                )
                self.assertEqual(dl.status_code, 200)
                self.assertIn("download_url", dl.json())
                self.assertTrue(dl.json()["download_url"].startswith("https://"))

    def test_uploads_without_cos_is_503(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("starlette TestClient not available")

        cfg = Config()
        with tempfile.TemporaryDirectory() as td:
            store = SQLiteStore(Path(td) / "t.db")
            rec = _rec(store, sid="sess_httptest000000000002")
            from sleuth.server.app import create_app

            with patch("sleuth.server.app.create_store", return_value=store), patch(
                "sleuth.server.app.load", return_value=cfg
            ), patch(
                "sleuth.files.mailbox.object_store_from_config",
                side_effect=CosNotConfigured("COS is not configured"),
            ):
                app = create_app(workdir=Path(td))
                client = TestClient(app)
                res = client.post(
                    f"/v1/sessions/{rec.id}/files/uploads",
                    headers={"X-User-Id": "alice"},
                    json={"filename": "a.txt", "mime": "text/plain", "size": 1},
                )
            self.assertEqual(res.status_code, 503)


class KbClientTests(unittest.TestCase):
    def setUp(self):
        reset_token_cache()

    def test_search_knowledge_parses_body(self):
        cfg = _cfg()
        captured = []

        class FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                url = req.full_url
                body = json.loads((req.data or b"{}").decode("utf-8"))
                captured.append((url, dict(req.header_items()), body))
                if "login" in url:
                    return FakeResp(
                        {
                            "returnCode": "SUC0000",
                            "body": {
                                "ragToken": "tok",
                                "expireTime": 1999999999,
                            },
                        }
                    )
                return FakeResp(
                    {
                        "returnCode": "SUC0000",
                        "body": [
                            {
                                "title": "t",
                                "fileName": "a.pdf",
                                "dmzUrl": "https://kb.example/dmz/a.pdf",
                                "fileUrl": "https://kb.example/a.pdf",
                                "paragraph": "p",
                                "rankScore": 0.5,
                            }
                        ],
                    }
                )

        hits = search_knowledge("C001", cfg, opener=FakeOpener())
        self.assertEqual(hits[0]["file_name"], "a.pdf")
        self.assertEqual(hits[0]["url"], "https://kb.example/dmz/a.pdf")
        self.assertEqual(len(captured), 2)
        login_url, _login_headers, login_body = captured[0]
        search_url, search_headers, search_body = captured[1]
        self.assertIn("login", login_url)
        self.assertEqual(login_body["openId"], "oid")
        self.assertEqual(login_body["serviceId"], "sid")
        self.assertIn("search", search_url)
        cookie = search_headers.get("Cookie") or search_headers.get("cookie")
        self.assertEqual(cookie, "ragToken=tok")
        self.assertEqual(search_body["question"], "C001")
        self.assertNotIn("topK", search_body)

    def test_service_config_knowledge_ids(self):
        cfg = _cfg()
        cfg.kb.knowledge_ids = "10752,10753"
        cfg.kb.sort_count = 5
        captured = []

        class FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                url = req.full_url
                body = json.loads((req.data or b"{}").decode("utf-8"))
                captured.append((url, body))
                if "login" in url:
                    return FakeResp(
                        {
                            "returnCode": "SUC0000",
                            "body": {"ragToken": "tok", "expireTime": 1999999999},
                        }
                    )
                return FakeResp({"returnCode": "SUC0000", "body": []})

        search_knowledge("C001", cfg, opener=FakeOpener())
        search_body = captured[1][1]
        sc = search_body["serviceConfig"]
        self.assertEqual(sc["sortConfig"]["sortCount"], 5)
        self.assertEqual(
            [x["knowledgeId"] for x in sc["recallConfig"]],
            ["10752", "10753"],
        )


if __name__ == "__main__":
    unittest.main()
