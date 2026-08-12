"""OpenAI 兼容 LLM 调用。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings


class LlmError(RuntimeError):
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
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LlmError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}") from exc
        data = resp.json()
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
