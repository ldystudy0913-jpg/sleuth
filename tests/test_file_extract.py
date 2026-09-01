"""Decrypt, extract, prompt injection, and extract concurrency."""
from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sleuth.config import Config, FilesConfig
from sleuth.files.cos import MemoryObjectStore
from sleuth.files.extract import Excerpt, extract_bytes, resolve_vision_prompt
from sleuth.files.ingest import reset_scheduler, wait_extracts
from sleuth.files.mailbox import (
    attachment_refs,
    files_prompt_block,
    ingest_user_file,
)
from sleuth.permission import Permission, Rule
from sleuth.storage.base import SessionRecord
from sleuth.storage.sqlite import SQLiteStore
from sleuth.tools.base import ToolContext
from sleuth.tools.read_session_file import ReadSessionFileTool


def _cfg() -> Config:
    cfg = Config()
    cfg.cos.secret_id = "sid"
    cfg.cos.secret_key = "skey"
    cfg.cos.region = "ap-guangzhou"
    cfg.cos.bucket = "demo-bucket"
    cfg.cos.endpoint = "https://cos.example"
    cfg.cos.path_prefix = "mailbox"
    cfg.files.max_bytes = 1_000_000
    cfg.files.max_count = 20
    cfg.files.require_encrypt = False
    cfg.files.image_mode = "vision"
    cfg.files.extract_concurrency = 2
    cfg.files.prompt_wait_s = 2.0
    return cfg


def _rec(store, sid="sess_extract00000000000001"):
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


class ExtractKindTests(unittest.TestCase):
    def test_plain_text(self):
        out = extract_bytes(
            "你好摘录".encode("utf-8"),
            mime="text/plain",
            filename="a.txt",
            config=_cfg(),
        )
        self.assertEqual(out.parser, "text")
        self.assertIn("你好", out.text)

    def test_image_off(self):
        cfg = _cfg()
        cfg.files.image_mode = "off"
        out = extract_bytes(
            b"\xff\xd8\xff" + b"\x00" * 20,
            mime="image/jpeg",
            filename="id.jpg",
            config=cfg,
        )
        self.assertIn("disabled", out.skipped)

    def test_vision_then_ocr_fallback(self):
        cfg = _cfg()

        def boom(*_a, **_k):
            raise RuntimeError("no vision")

        def ocr(_data):
            return "姓名 张三"

        with patch("sleuth.files.extract.vision_image_text", boom), patch(
            "sleuth.files.extract.ocr_image_text", ocr
        ):
            out = extract_bytes(
                b"\xff\xd8\xff" + b"\x00" * 20,
                mime="image/jpeg",
                filename="id.jpg",
                config=cfg,
            )
        self.assertEqual(out.parser, "rapidocr")
        self.assertIn("张三", out.text)

    def test_vision_success(self):
        cfg = _cfg()
        with patch(
            "sleuth.files.extract.vision_image_text", return_value="证号 110"
        ), patch(
            "sleuth.files.extract.ocr_image_text",
            side_effect=AssertionError("ocr should not run"),
        ):
            out = extract_bytes(
                b"\xff\xd8\xff" + b"\x00" * 20,
                mime="image/jpeg",
                filename="id.jpg",
                config=cfg,
            )
        self.assertEqual(out.parser, "vision")
        self.assertIn("110", out.text)

    def test_default_vision_prompt_describes_scene(self):
        prompt = FilesConfig().vision_prompt
        self.assertIn("展示", prompt)
        self.assertIn("可见文字", prompt)
        preamble = FilesConfig().prompt_preamble
        self.assertIn("vision description", preamble.lower())
        self.assertIn("question", preamble.lower())
        self.assertIn("parser={parser}", FilesConfig().prompt_item_line)

    def test_files_prompt_includes_parser_and_description(self):
        cfg = _cfg()

        class Sess:
            config = cfg
            id = "sess_p"
            _files = [
                {
                    "id": "file_img",
                    "role": "user",
                    "filename": "AI生成.jpg",
                    "mime": "image/jpeg",
                    "size": 12,
                    "status": "ready",
                    "excerpt_status": "ok",
                    "excerpt": {
                        "text": "画面里有人在厨房炒菜。水印：豆包AI生成",
                        "parser": "vision",
                        "truncated": False,
                    },
                }
            ]
            _prompt_file_ids = None

        block = files_prompt_block(Sess())
        self.assertIn("炒菜", block)
        self.assertIn("parser=vision", block)
        self.assertIn("vision description", block.lower())
        self.assertIn("do not say the system failed to attach", block.lower())

    def test_empty_pdf_rasters_then_vision(self):
        cfg = _cfg()
        from types import ModuleType, SimpleNamespace
        import sys

        fake = ModuleType("pypdf")

        class PdfReader:
            def __init__(self, _buf):
                page = SimpleNamespace(extract_text=lambda: "")
                self.pages = [page]

        fake.PdfReader = PdfReader
        captured = {}

        def vis(_data, _mime, _config, prompt=None):
            captured["prompt"] = prompt
            return "a person waving"

        with patch.dict(sys.modules, {"pypdf": fake}), patch(
            "sleuth.files.extract.render_pdf_pages",
            return_value=([b"\x89PNG" + b"\x00" * 8], "", False),
        ), patch(
            "sleuth.files.extract.vision_image_text", vis
        ), patch(
            "sleuth.files.extract.ocr_image_text",
            side_effect=AssertionError("ocr should not run"),
        ):
            out = extract_bytes(
                b"%PDF-1.4 empty",
                mime="application/pdf",
                filename="scan.pdf",
                config=cfg,
            )
        self.assertIn("waving", out.text)
        self.assertIn("pypdfium2", out.parser)
        self.assertIn("vision", out.parser)
        self.assertIn("展示", captured.get("prompt") or "")

    def test_resolve_focus_prompt_includes_question(self):
        cfg = _cfg()
        text = resolve_vision_prompt(cfg, "这张图片在做什么")
        self.assertIn("这张图片在做什么", text)


class EncryptedIngestTests(unittest.TestCase):
    def setUp(self):
        reset_scheduler()

    def tearDown(self):
        wait_extracts(2.0)

    def test_complete_pending_then_excerpt(self):
        cfg = _cfg()
        cfg.files.require_encrypt = True
        cfg.files.sm4_key = "0123456789abcdef"
        plain = "客户姓名：余某".encode("utf-8")
        mem = MemoryObjectStore()
        with tempfile.TemporaryDirectory() as td:
            store = SQLiteStore(Path(td) / "t.db")
            rec = _rec(store)
            item = ingest_user_file(
                config=cfg,
                store=store,
                rec=rec,
                filename="kyc.txt",
                mime="text/plain",
                data=plain,
                object_store=mem,
            )
            self.assertEqual(item["status"], "ready")
            self.assertEqual(item["excerpt_status"], "pending")
            self.assertNotEqual(mem.get_bytes(item["object_key"]), plain)
            self.assertTrue(wait_extracts(3.0))
            rec = store.get_session(rec.id)
            stored = rec.metadata["files"][0]
            self.assertEqual(stored["excerpt_status"], "ok")
            self.assertIn("余某", stored["excerpt"]["text"])

            db = store

            class Sess:
                config = cfg
                id = rec.id
                user_id = "alice"
                store = db
                _files = []
                _prompt_file_ids = None
                _object_store = mem

            block = files_prompt_block(Sess())
            self.assertIn("余某", block)
            refs = attachment_refs(config=cfg, session=Sess(), object_store=mem)
            self.assertIn("余某", refs[0]["excerpt"])
            self.assertTrue(refs[0]["encrypted"])

    def test_plaintext_legacy_still_extracts(self):
        cfg = _cfg()
        mem = MemoryObjectStore()
        with tempfile.TemporaryDirectory() as td:
            store = SQLiteStore(Path(td) / "t.db")
            rec = _rec(store, sid="sess_extract00000000000002")
            ingest_user_file(
                config=cfg,
                store=store,
                rec=rec,
                filename="notes.txt",
                mime="text/plain",
                data=b"hello",
                object_store=mem,
            )
            self.assertTrue(wait_extracts(3.0))
            rec = store.get_session(rec.id)
            self.assertEqual(rec.metadata["files"][0]["excerpt"]["text"], "hello")

    def test_read_session_file_tool(self):
        cfg = _cfg()
        mem = MemoryObjectStore()
        mem.put_bytes(key="k/a.txt", data=b"body-text", mime="text/plain")

        class Sess:
            config = cfg
            id = "sess_read"
            user_id = "alice"
            store = None
            _files = [
                {
                    "id": "file_read1",
                    "role": "user",
                    "filename": "a.txt",
                    "mime": "text/plain",
                    "size": 9,
                    "object_key": "k/a.txt",
                    "status": "ready",
                    "excerpt_status": "pending",
                }
            ]
            _object_store = mem

        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        result = ReadSessionFileTool().execute({"file_id": "file_read1"}, ctx)
        self.assertFalse(result.is_error)
        payload = json.loads(result.output)
        self.assertEqual(payload["text"], "body-text")
        self.assertEqual(payload["excerpt_status"], "ok")
        self.assertFalse(payload["focused"])

    def test_question_reruns_vision_without_overwriting_excerpt(self):
        cfg = _cfg()
        mem = MemoryObjectStore()
        mem.put_bytes(
            key="k/pic.jpg",
            data=b"\xff\xd8\xff" + b"\x00" * 20,
            mime="image/jpeg",
        )
        captured = {}

        def vis(_data, _mime, _config, prompt=None):
            captured["prompt"] = prompt
            return "someone cooking in a kitchen"

        class Sess:
            config = cfg
            id = "sess_focus"
            user_id = "alice"
            store = None
            _files = [
                {
                    "id": "file_img1",
                    "role": "user",
                    "filename": "AI生成.jpg",
                    "mime": "image/jpeg",
                    "size": 23,
                    "object_key": "k/pic.jpg",
                    "status": "ready",
                    "excerpt_status": "ok",
                    "excerpt": {
                        "text": "豆包AI生成",
                        "parser": "rapidocr",
                        "truncated": False,
                    },
                }
            ]
            _object_store = mem

        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        with patch("sleuth.files.extract.vision_image_text", vis), patch(
            "sleuth.files.extract.ocr_image_text",
            side_effect=AssertionError("ocr should not run"),
        ):
            result = ReadSessionFileTool().execute(
                {"file_id": "file_img1", "question": "这张图片在做什么"},
                ctx,
            )
        self.assertFalse(result.is_error)
        payload = json.loads(result.output)
        self.assertTrue(payload["focused"])
        self.assertIn("cooking", payload["text"])
        self.assertIn("这张图片在做什么", captured.get("prompt") or "")
        self.assertEqual(Sess._files[0]["excerpt"]["text"], "豆包AI生成")

    def test_question_rereads_long_text_without_overwriting(self):
        cfg = _cfg()
        cfg.files.excerpt_max_chars = 20
        cfg.files.excerpt_reread_max_chars = 200
        full = "abcdefghijklmnopqrstuvwxyz0123456789"
        mem = MemoryObjectStore()
        mem.put_bytes(key="k/long.txt", data=full.encode("utf-8"), mime="text/plain")

        class Sess:
            config = cfg
            id = "sess_long"
            user_id = "alice"
            store = None
            _files = [
                {
                    "id": "file_long",
                    "role": "user",
                    "filename": "long.txt",
                    "mime": "text/plain",
                    "size": len(full),
                    "object_key": "k/long.txt",
                    "status": "ready",
                    "excerpt_status": "ok",
                    "excerpt": {
                        "text": full[:20],
                        "parser": "text",
                        "truncated": True,
                    },
                }
            ]
            _object_store = mem

        ctx = ToolContext(
            workdir=Path("."),
            permission=Permission(rules=[Rule("*", "*", "allow")]),
            session=Sess(),
        )
        cached = ReadSessionFileTool().execute({"file_id": "file_long"}, ctx)
        self.assertEqual(json.loads(cached.output)["text"], full[:20])
        focused = ReadSessionFileTool().execute(
            {"file_id": "file_long", "question": "文件后半段写了什么"},
            ctx,
        )
        payload = json.loads(focused.output)
        self.assertTrue(payload["focused"])
        self.assertEqual(payload["text"], full)
        self.assertEqual(Sess._files[0]["excerpt"]["text"], full[:20])


class ExtractConcurrencyTests(unittest.TestCase):
    def setUp(self):
        reset_scheduler()

    def tearDown(self):
        wait_extracts(5.0)
        reset_scheduler()

    def test_semaphore_caps_parallel_extract(self):
        cfg = _cfg()
        cfg.files.extract_concurrency = 2
        current = 0
        max_seen = 0
        lock = threading.Lock()

        def slow(_data, **_kwargs):
            nonlocal current, max_seen
            with lock:
                current += 1
                max_seen = max(max_seen, current)
            time.sleep(0.15)
            with lock:
                current -= 1
            return Excerpt(text="x", parser="fake")

        mem = MemoryObjectStore()
        with tempfile.TemporaryDirectory() as td, patch(
            "sleuth.files.ingest.extract_bytes", side_effect=slow
        ):
            store = SQLiteStore(Path(td) / "t.db")
            rec = _rec(store, sid="sess_extract00000000000003")
            for i in range(4):
                rec = store.get_session(rec.id)
                ingest_user_file(
                    config=cfg,
                    store=store,
                    rec=rec,
                    filename=f"n{i}.txt",
                    mime="text/plain",
                    data=b"x",
                    object_store=mem,
                )
            self.assertTrue(wait_extracts(5.0))
            self.assertLessEqual(max_seen, 2)
            self.assertGreaterEqual(max_seen, 1)


class OptionalParserTests(unittest.TestCase):
    def test_xlsx_if_openpyxl(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl not installed")
        wb = Workbook()
        wb.active["A1"] = "ubo"
        buf = io.BytesIO()
        wb.save(buf)
        out = extract_bytes(
            buf.getvalue(),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="t.xlsx",
            config=_cfg(),
        )
        self.assertEqual(out.parser, "openpyxl")
        self.assertIn("ubo", out.text)


if __name__ == "__main__":
    unittest.main()
