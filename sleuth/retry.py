"""Retry with exponential backoff — port of opencode `session/retry.ts`.

Transient provider failures (5xx, rate limits, overloaded) are retried with
backoff. Context-overflow style errors are never retried.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .provider.base import ProviderError

RETRY_INITIAL_DELAY = 2.0  # seconds
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY_NO_HEADERS = 30.0
RETRY_MAX_DELAY = 2_147_483_647 / 1000.0  # match opencode's setTimeout cap
DEFAULT_MAX_ATTEMPTS = 3


def _cap(seconds: float) -> float:
    return min(seconds, RETRY_MAX_DELAY)


def delay(attempt: int, error: Optional[ProviderError] = None) -> float:
    """Seconds to wait before the next attempt (1-indexed attempt)."""
    if error is not None and error.response_headers:
        headers = {str(k).lower(): v for k, v in error.response_headers.items()}
        retry_after_ms = headers.get("retry-after-ms")
        if retry_after_ms is not None:
            try:
                return _cap(float(retry_after_ms) / 1000.0)
            except (TypeError, ValueError):
                pass
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                return _cap(float(retry_after))
            except (TypeError, ValueError):
                pass
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(str(retry_after))
                wait = dt.timestamp() - time.time()
                if wait > 0:
                    return _cap(wait)
            except (TypeError, ValueError, OverflowError):
                pass
        return _cap(RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)))

    return _cap(
        min(
            RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)),
            RETRY_MAX_DELAY_NO_HEADERS,
        )
    )


def retryable(error: BaseException) -> Optional[str]:
    """Return a human message if `error` should be retried, else None.

    Port of opencode `retryable()` — skips context overflow; retries 5xx and
    rate-limit / overloaded patterns.
    """
    if not isinstance(error, ProviderError):
        msg = str(error).lower()
        if any(p in msg for p in ("rate limit", "too many requests", "overloaded", "unavailable")):
            return str(error)
        return None

    if error.is_overflow:
        return None

    status = error.status_code
    if status is not None and status >= 500:
        return error.message or "Provider server error"
    if status in (408, 429):
        return error.message or "Rate limited"
    if error.is_retryable:
        return error.message or "Transient provider error"

    body = error.response_body or ""
    if "FreeUsageLimitError" in body or "GoUsageLimitError" in body:
        return error.message or "Usage limit reached"

    msg = (error.message or "").lower()
    if any(
        p in msg
        for p in (
            "rate increased too quickly",
            "rate limit",
            "too many requests",
            "overloaded",
            "temporarily unavailable",
        )
    ):
        return error.message or "Rate limited"

    try:
        parsed = (
            json.loads(error.message)
            if error.message and error.message.strip().startswith("{")
            else None
        )
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        code = str(parsed.get("code") or "")
        err = parsed.get("error") or {}
        if isinstance(err, dict):
            err_type = str(err.get("type") or "")
            err_code = str(err.get("code") or "")
            if err_type == "too_many_requests" or "rate_limit" in err_code:
                return "Rate Limited"
        if "exhausted" in code or "unavailable" in code:
            return "Provider is overloaded"

    return None


def sleep_interruptible(seconds: float, abort=None) -> bool:
    """Sleep in short slices so abort can interrupt. Returns False if aborted."""
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        if abort is not None and getattr(abort, "is_set", lambda: False)():
            return False
        time.sleep(min(0.2, end - time.monotonic()))
    return True
