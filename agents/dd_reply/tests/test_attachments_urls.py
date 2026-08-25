"""Tests for session mailbox URL attachments."""
from __future__ import annotations

import unittest

from dd_reply.attachments import load_from_urls
from dd_reply.config import Settings


class FakeStream:
    def __init__(self, data: bytes, status: int = 200):
        self.status_code = status
        self.headers = {"content-length": str(len(data))}
        self._data = data

    def iter_bytes(self, n):
        yield self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeClient:
    def __init__(self, data: bytes = b"hello notes"):
        self._data = data

    def stream(self, method, url):
        return FakeStream(self._data)

    def close(self):
        return None


class LoadFromUrlsTests(unittest.TestCase):
    def test_https_excerpt(self):
        settings = Settings(kb_api_url="http://kb.test/search")
        bundle = load_from_urls(
            [
                {
                    "file_id": "file_1",
                    "filename": "notes.txt",
                    "url": "https://cos.example/notes.txt",
                    "size": 11,
                }
            ],
            settings,
            client=FakeClient(),
        )
        self.assertEqual(len(bundle.excerpts), 1)
        self.assertIn("hello", bundle.excerpts[0].text)
        self.assertFalse(bundle.skipped)

    def test_prefers_excerpt_and_skips_encrypted_without_excerpt(self):
        settings = Settings(kb_api_url="http://kb.test/search")
        bundle = load_from_urls(
            [
                {
                    "filename": "id.pdf",
                    "url": "https://cos.example/id.pdf",
                    "encrypted": True,
                    "excerpt": "姓名 余某",
                    "truncated": False,
                },
                {
                    "filename": "cipher.bin",
                    "url": "https://cos.example/cipher.bin",
                    "encrypted": True,
                },
            ],
            settings,
            client=FakeClient(b"\x00\x01\x02"),
        )
        self.assertEqual(len(bundle.excerpts), 1)
        self.assertIn("余某", bundle.excerpts[0].text)
        self.assertTrue(any("encrypted" in s for s in bundle.skipped))

    def test_rejects_data_url(self):
        settings = Settings(kb_api_url="http://kb.test/search")
        bundle = load_from_urls(
            [{"filename": "x", "url": "data:text/plain,abc"}],
            settings,
            client=FakeClient(),
        )
        self.assertEqual(bundle.excerpts, [])
        self.assertTrue(any("data/file" in s for s in bundle.skipped))


if __name__ == "__main__":
    unittest.main()
