"""OpenAI 兼容 LLM 调用。"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings


class LlmError(RuntimeError):
    pass


def _trace_headers(headers: Dict[str, str]) -> Dict[str, str]:
    try:
        from .logtrace import is_enabled, outbound_headers

        if is_enabled():
            return outbound_headers(headers)
    except Exception:
        pass
    return headers


def _record_http(method: str, url: str, headers: Dict[str, str], start: float, code: str) -> None:
    try:
        from .logtrace import record_outbound_http

        record_outbound_http(method, url, headers, start, code)
    except Exception:
        pass


def chat_completion(
    messages: List[Dict[str, str]],
    settings: Settings,
    *,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> str:
    if not settings.llm_configured():
        raise LlmError(
            "LLM not configured: set DD_REPLY_LLM_BASE_URL and DD_REPLY_LLM_API_KEY"
        )
    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    payload: Dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
    }
    headers = _trace_headers({
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    })
    start = time.time()
    code = "SUC0000"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                code = "ERROR"
                raise LlmError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}") from exc
            data = resp.json()
    except Exception:
        code = "ERROR"
        raise
    finally:
        _record_http("POST", url, headers, start, code)
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"unexpected LLM response: {json.dumps(data)[:500]}") from exc


def mockable_generate(
    messages: List[Dict[str, str]],
    settings: Settings,
    *,
    mock_fn=None,
) -> str:
    if mock_fn is not None:
        return str(mock_fn(messages))
    return chat_completion(messages, settings)
