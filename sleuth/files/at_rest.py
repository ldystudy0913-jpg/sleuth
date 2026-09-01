"""Encrypt plaintext for COS and decrypt on the way out. Knobs come from Config."""
from __future__ import annotations

from typing import Tuple

from ..config import Config
from .errors import MailboxError
from .crypto_sm4 import Sm4CbcError, sm4_cbc_decrypt, sm4_cbc_encrypt
from . import settings


def store_payload(plain: bytes, config: Config) -> Tuple[bytes, bool]:
    key = settings.sm4_key(config)
    if settings.require_encrypt(config) and not key:
        raise MailboxError(settings.err_sm4_key(config), 503)
    if not key or not settings.require_encrypt(config):
        return plain, False
    return sm4_cbc_encrypt(plain, key), True


def restore_plaintext(raw: bytes, *, encrypted: bool, config: Config) -> bytes:
    if not encrypted:
        return raw
    key = settings.sm4_key(config)
    if not key:
        raise MailboxError(settings.err_sm4_key(config), 503)
    try:
        return sm4_cbc_decrypt(raw, key)
    except Sm4CbcError as exc:
        raise MailboxError(f"sm4 decrypt failed: {exc}") from exc


def put_mime(config: Config, *, encrypted: bool, original_mime: str) -> str:
    if encrypted:
        return settings.cipher_mime(config) or original_mime
    return original_mime
