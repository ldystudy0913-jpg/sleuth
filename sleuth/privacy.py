"""Output desensitization — scrub PII before Sleuth persists or displays text.

Covers common mainland-CN patterns: ID card, mobile, bank card, password labels,
and labeled home addresses. Enable/disable via Config.output_desensitize
(SLEUTH_OUTPUT_DESENSITIZE, default on).
"""
from __future__ import annotations

import re
from typing import Optional

from .config import PrivacyConfig

# Password / credential labels
_RE_PASSWORD = re.compile(
    r"(?i)((?:密码|口令|pwd|password)\s*[：:=]\s*)(\S+)",
)

# http(s) URLs — digit masks must not run inside these (file ids look like cards).
_RE_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)

# Mainland 18-digit ID (allow trailing X)
_RE_ID = re.compile(
    r"(?<![\d*Xx])"
    r"([1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])"
    r"(?![\d*])",
)

# Mobile 11-digit
_RE_MOBILE = re.compile(
    r"(?<![\d*])(1[3-9]\d{9})(?![\d*])",
)

# Bank card-like: 13–19 digits with optional spaces/hyphens (skip if already *)
_RE_BANK = re.compile(
    r"(?<![\d*])((?:\d[ -]?){12,18}\d)(?![\d*])",
)

# Labeled address values (avoid bare 地址 in 经营范围 without colon value)
_RE_ADDRESS = re.compile(
    r"((?:家庭住址|户籍地址|居住地址|详细地址|住址|联系地址|地址)\s*[：:]\s*)"
    r"([^\n，。；;]{8,})"
)


def _mask_id(m: re.Match[str]) -> str:
    s = m.group(1)
    if "*" in s:
        return s
    if len(s) < 6:
        return "*" * len(s)
    return s[:3] + ("*" * (len(s) - 5)) + s[-2:]


def _mask_mobile(m: re.Match[str]) -> str:
    s = m.group(1)
    if "*" in s:
        return s
    return s[:3] + "****" + s[-4:]


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)


def _is_unix_ms(digits: str, privacy: PrivacyConfig) -> bool:
    if not privacy.skip_unix_ms:
        return False
    if len(digits) != 13:
        return False
    try:
        n = int(digits)
    except ValueError:
        return False
    return privacy.unix_ms_min <= n <= privacy.unix_ms_max


def _mask_bank(m: re.Match[str], privacy: PrivacyConfig) -> str:
    raw = m.group(1)
    if "*" in raw:
        return raw
    digits = _digits_only(raw)
    # Skip 18-digit ID-shaped numbers (already handled); skip short runs
    if len(digits) < 13 or len(digits) > 19:
        return raw
    if _is_unix_ms(digits, privacy):
        return raw
    if len(digits) == 18 and re.fullmatch(
        r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
        digits,
        re.I,
    ):
        return raw
    return "*" * (len(digits) - 4) + digits[-4:]


def _mask_password(m: re.Match[str]) -> str:
    return m.group(1) + "***"


def _mask_address(m: re.Match[str]) -> str:
    return m.group(1) + "***"


def _protect_urls(text: str) -> tuple:
    urls: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        urls.append(m.group(0))
        return f"\x00URL{len(urls) - 1}\x00"

    return _RE_URL.sub(_stash, text), urls


def _restore_urls(text: str, urls: list[str]) -> str:
    for i, url in enumerate(urls):
        text = text.replace(f"\x00URL{i}\x00", url)
    return text


def contains_raw_pii(text: str, *, privacy: Optional[PrivacyConfig] = None) -> bool:
    """True when text still contains an unmasked ID, mobile, or bank-card run."""
    if not text:
        return False
    priv = privacy or PrivacyConfig()
    protected, _ = _protect_urls(text)
    if _RE_ID.search(protected) or _RE_MOBILE.search(protected):
        return True
    for m in _RE_BANK.finditer(protected):
        digits = _digits_only(m.group(1))
        if "*" in m.group(1):
            continue
        if _is_unix_ms(digits, priv):
            continue
        if len(digits) < 13 or len(digits) > 19:
            continue
        return True
    return False


def desensitize_text(text: str, *, privacy: Optional[PrivacyConfig] = None) -> str:
    """Return text with common PII patterns masked. Empty/None-safe for str only."""
    if not text:
        return text
    priv = privacy or PrivacyConfig()
    out, urls = _protect_urls(text)
    out = _RE_PASSWORD.sub(_mask_password, out)
    out = _RE_ID.sub(_mask_id, out)
    out = _RE_MOBILE.sub(_mask_mobile, out)
    out = _RE_BANK.sub(lambda m: _mask_bank(m, priv), out)
    out = _RE_ADDRESS.sub(_mask_address, out)
    return _restore_urls(out, urls)


def desensitize_optional(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return desensitize_text(text)


def maybe_desensitize(
    text: str, *, enabled: bool = True, privacy: Optional[PrivacyConfig] = None
) -> str:
    if not enabled or not text:
        return text
    return desensitize_text(text, privacy=privacy)
