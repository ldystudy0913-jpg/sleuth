"""OpenAI-compatible chat/completions via urllib. No extra HTTP library.

Agent ``{PKG}_LLM_*`` wins when complete. Otherwise apply Sleuth-injected
``sleuth_llm_json`` (session model). Temperature / timeout / json_mode stay
on the agent Settings object.
"""
from __future__ import annotations

import copy
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .config import Settings

LlmFn = Callable[[List[Dict[str, str]], Settings], str]


class LlmError(RuntimeError):
    """Remote LLM HTTP / protocol failure."""


def parse_sleuth_llm_json(raw: str) -> Dict[str, str]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or data.get("apiKey") or "").strip()
    base_url = str(
        data.get("base_url") or data.get("baseURL") or data.get("baseUrl") or ""
    ).strip().rstrip("/")
    if not (model and api_key and base_url):
        return {}
    return {"model": model, "api_key": api_key, "base_url": base_url}


def settings_with_llm_json(settings: Settings, sleuth_llm_json: str = "") -> Settings:
    """Prefer agent LLM env; fill from Sleuth injection only when env is incomplete."""
    if settings.llm_configured():
        return settings
    data = parse_sleuth_llm_json(sleuth_llm_json)
    if not data:
        return settings
    out = copy.copy(settings)
    out.llm_model = data["model"]
    out.llm_api_key = data["api_key"]
    out.llm_base_url = data["base_url"]
    return out


def _llm_missing_detail(settings: Settings) -> str:
    prefix = str(getattr(settings, "env_prefix", "") or "AGENT").strip() or "AGENT"
    return (
        f"LLM not configured: set {prefix}_LLM_BASE_URL, {prefix}_LLM_API_KEY, "
        f"{prefix}_LLM_MODEL (or leave them empty and call via Sleuth)"
    )


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            err_body = ""
        raise LlmError(f"LLM HTTP {exc.code}: {err_body}") from exc
    except urllib.error.URLError as exc:
        raise LlmError(f"LLM request failed: {exc}") from exc
    if int(status) >= 400:
        raise LlmError(f"LLM HTTP {status}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LlmError("LLM response is not JSON") from exc


def chat_completion(messages: List[Dict[str, str]], settings: Settings) -> str:
    if not settings.llm_configured():
        raise LlmError(_llm_missing_detail(settings))
    url = f"{settings.llm_base_url}/chat/completions"
    payload: Dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": float(settings.llm_temperature),
    }
    if settings.llm_json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        data = _post_json(url, payload, headers, float(settings.llm_timeout))
    except LlmError:
        if not settings.llm_json_mode:
            raise
        payload.pop("response_format", None)
        data = _post_json(url, payload, headers, float(settings.llm_timeout))
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"unexpected LLM response: {json.dumps(data)[:500]}") from exc


def parse_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise LlmError("LLM returned empty content")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        data = json.loads(fenced.group(1))
        if isinstance(data, dict):
            return data
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise LlmError("LLM content is not a JSON object")


def complete_json(
    messages: List[Dict[str, str]],
    settings: Settings,
    *,
    llm_fn: Optional[LlmFn] = None,
) -> Dict[str, Any]:
    text = llm_fn(messages, settings) if llm_fn is not None else chat_completion(messages, settings)
    return parse_json_object(text)
