"""Attachment pipeline tests (in-memory stores, size caps)."""
from __future__ import annotations

import unittest

from dd_check.attachments import (
    AttachmentMeta,
    AttachmentPipeline,
    InMemoryMetaStore,
    InMemoryObjectStore,
)
from dd_check.attachments.crypto_sm4 import sm4_cbc_encrypt
from dd_check.config import Settings


class AttachmentPipelineTests(unittest.TestCase):
    def test_decrypt_and_excerpt(self):
        key = "0123456789abcdef"
        plain = ("报告附件文本。" * 100).encode("utf-8")
        cipher = sm4_cbc_encrypt(plain, key)
        settings = Settings(
            ecs_emode_b_key=key,
            attachment_excerpt_max_chars=50,
        )
        pipe = AttachmentPipeline(
            settings,
            meta_store=InMemoryMetaStore(
                {"INV1": [AttachmentMeta(file_id="f1", location_path="cos/a.bin")]}
            ),
            object_store=InMemoryObjectStore({"cos/a.bin": cipher}),
        )
        bundle = pipe.run("INV1")
        self.assertEqual(len(bundle.excerpts), 1)
        self.assertTrue(bundle.excerpts[0].truncated)
        self.assertTrue(bundle.excerpts[0].text.startswith("报告附件文本"))

    def test_skip_without_invest(self):
        pipe = AttachmentPipeline(Settings(ecs_emode_b_key="0123456789abcdef"))
        bundle = pipe.run("")
        self.assertTrue(bundle.skipped)

    def test_size_cap(self):
        key = "0123456789abcdef"
        settings = Settings(ecs_emode_b_key=key, attachment_max_bytes=32)
        # encrypt small then pretend larger by concatenating — decrypt will fail length;
        # instead feed oversized ciphertext blocks
        cipher = sm4_cbc_encrypt(b"hello world!!!!!", key)  # padded
        huge = cipher * 10
        pipe = AttachmentPipeline(
            settings,
            meta_store=InMemoryMetaStore(
                {"INV1": [AttachmentMeta(file_id="f1", location_path="p")]}
            ),
            object_store=InMemoryObjectStore({"p": huge}),
        )
        bundle = pipe.run("INV1")
        self.assertEqual(bundle.excerpts, [])
        self.assertTrue(any("ATTACHMENT_MAX_BYTES" in s or "exceeds" in s for s in bundle.skipped))


if __name__ == "__main__":
    unittest.main()
