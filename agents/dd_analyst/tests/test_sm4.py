"""SM4 encrypt/decrypt roundtrip (Java EncryptFileB compatible convention)."""
from __future__ import annotations

import unittest

from dd_check.attachments.crypto_sm4 import Sm4CbcError, sm4_cbc_decrypt, sm4_cbc_encrypt


class Sm4Tests(unittest.TestCase):
    def test_roundtrip(self):
        key = "0123456789abcdef"  # 16 bytes
        plain = "附件内容：客户余某，地址杭州。伊朗相关测试。".encode("utf-8")
        cipher = sm4_cbc_encrypt(plain, key)
        self.assertNotEqual(cipher, plain)
        self.assertEqual(len(cipher) % 16, 0)
        out = sm4_cbc_decrypt(cipher, key)
        self.assertEqual(out, plain)

    def test_empty_key(self):
        with self.assertRaises(Sm4CbcError):
            sm4_cbc_decrypt(b"0" * 16, "")

    def test_bad_length(self):
        with self.assertRaises(Sm4CbcError):
            sm4_cbc_decrypt(b"short", "0123456789abcdef")


if __name__ == "__main__":
    unittest.main()
