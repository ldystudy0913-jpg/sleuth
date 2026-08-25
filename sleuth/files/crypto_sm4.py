"""SM4-CBC decrypt aligned with Java EncryptFileB (CMB SM4 CBC, key == IV).

Java reference:
  byte[] decode = Utf8.encode(key);
  SM4_INSTANCE.CMBSM4DecryptWithCBC(decode, decode, ciphertextBytes);

Key and IV are both the UTF-8 bytes of the configured key string (16 bytes).
"""
from __future__ import annotations

from .sm4_core import sm4_cbc_decrypt_bytes, sm4_cbc_encrypt_bytes


class Sm4CbcError(Exception):
    pass


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise Sm4CbcError("empty plaintext")
    pad = data[-1]
    if pad < 1 or pad > 16:
        raise Sm4CbcError(f"invalid PKCS7 pad byte: {pad}")
    if data[-pad:] != bytes([pad]) * pad:
        raise Sm4CbcError("invalid PKCS7 padding")
    return data[:-pad]


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _key16(key: str) -> bytes:
    if not key:
        raise Sm4CbcError("SM4 key is empty")
    key_bytes = key.encode("utf-8")
    if len(key_bytes) > 16:
        key_bytes = key_bytes[:16]
    elif len(key_bytes) < 16:
        raise Sm4CbcError(f"SM4 key must be 16 bytes UTF-8, got {len(key_bytes)}")
    return key_bytes


def sm4_cbc_decrypt(ciphertext: bytes, key: str) -> bytes:
    key_bytes = _key16(key)
    if len(ciphertext) % 16 != 0:
        raise Sm4CbcError(f"ciphertext length must be multiple of 16, got {len(ciphertext)}")
    plain_padded = sm4_cbc_decrypt_bytes(ciphertext, key_bytes, key_bytes)
    return _pkcs7_unpad(plain_padded)


def sm4_cbc_encrypt(plaintext: bytes, key: str) -> bytes:
    key_bytes = _key16(key)
    return sm4_cbc_encrypt_bytes(_pkcs7_pad(plaintext), key_bytes, key_bytes)
