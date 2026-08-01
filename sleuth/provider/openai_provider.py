"""OpenAI-compatible provider.

Works against the OpenAI Chat Completions API and any compatible gateway
(OpenRouter, Together, Groq, local llama.cpp server, ...). Unlike the
This provider also defines our canonical wire format, so the message
translation here is the identity mapping for the common case:

  canonical TextBlock      -> {role, content: str}
  canonical ToolUseBlock   -> assistant message with tool_calls[]
  canonical ToolResultBlock -> a separate {role:"tool", tool_call_id, content}
  attachments / FileBlock  -> follow-up user message with image_url parts

Tool-call argument JSON is streamed as function.arguments fragments that we
concatenate and parse once the call closes, then emit as a ToolUse event.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from ..messages import FileBlock, Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..usage import extract_openai_chunk_usage, normalize_usage
from .base import ProviderError, ReasoningDelta, Stop, TextDelta, ToolUse


class OpenAIProvider:
    id = "openai"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        try:
            import openai  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment error
            raise ProviderError(
                "the 'openai' package is required: pip install openai"
            ) from exc
        self._openai = openai
        kwargs: Dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    # -- canonical -> openai wire --

    def _to_wire(self, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for msg in messages:
            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            tool_results: List[Dict[str, Any]] = []
            attachments: List[Dict[str, Any]] = []
            file_parts: List[FileBlock] = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input),
                            },
                        }
                    )
                elif isinstance(block, ToolResultBlock):
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id,
                            "content": block.content,
                        }
                    )
                    for att in block.attachments or []:
                        attachments.append(att)
                elif isinstance(block, FileBlock):
                    file_parts.append(block)

            if tool_results:
                out.extend(tool_results)
                # Multimodal attachments can't ride on role:tool; follow with
                # a user message containing image_url / file parts (opencode).
                vision = _attachments_to_content(attachments)
                if vision:
                    out.append({"role": "user", "content": vision})
                continue

            entry: Dict[str, Any] = {"role": msg.role}
            joined = "".join(text_parts)
            if file_parts or (msg.role == "user" and _looks_multimodal(msg)):
                content_list: List[Dict[str, Any]] = []
                if joined:
                    content_list.append({"type": "text", "text": joined})
                content_list.extend(_file_blocks_to_content(file_parts))
                entry["content"] = content_list if content_list else joined
            elif msg.role == "assistant":
                if joined:
                    entry["content"] = joined
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                if "content" not in entry and "tool_calls" not in entry:
                    entry["content"] = ""
            else:
                entry["content"] = joined
            out.append(entry)
        return out

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for t in tools:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters_json_schema", {}),
                    },
                }
            )
        return out

    def stream(
        self,
        *,
        system: str,
        messages: List[Message],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> Iterator[Any]:
        wire = [{"role": "system", "content": system}] + self._to_wire(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        active: Dict[int, Dict[str, Any]] = {}

        try:
            kwargs: Dict[str, Any] = dict(
                model=model,
                messages=wire,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )
            if openai_tools:
                kwargs["tools"] = openai_tools

            try:
                stream = self._client.chat.completions.create(**kwargs)
            except TypeError:
                # Older gateways may reject stream_options
                kwargs.pop("stream_options", None)
                stream = self._client.chat.completions.create(**kwargs)

            stop_reason = "stop"
            usage_raw: Dict[str, Any] = {}

            for chunk in stream:
                chunk_usage = extract_openai_chunk_usage(chunk)
                if chunk_usage:
                    usage_raw = chunk_usage

                choice = chunk.choices[0] if chunk.choices else None
                if choice is None:
                    continue
                delta = choice.delta

                if delta.content:
                    yield TextDelta(text=delta.content)

                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ReasoningDelta(text=reasoning)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        slot = active.setdefault(
                            idx,
                            {
                                "id": None,
                                "name": None,
                                "arguments": "",
                            },
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

                if choice.finish_reason:
                    stop_reason = choice.finish_reason

            for idx in sorted(active):
                slot = active[idx]
                raw = slot["arguments"] or ""
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = {"_raw": raw}
                yield ToolUse(
                    id=slot["id"] or f"call_{idx}",
                    name=slot["name"] or "unknown",
                    input=parsed,
                )

            yield Stop(reason=stop_reason, usage=normalize_usage(usage_raw))
        except self._openai.APIStatusError as exc:  # type: ignore[attr-defined]
            status = getattr(exc, "status_code", None)
            headers = None
            try:
                raw_h = getattr(exc, "headers", None) or getattr(exc, "response", None)
                if raw_h is not None and hasattr(raw_h, "headers"):
                    headers = {str(k): str(v) for k, v in raw_h.headers.items()}
                elif isinstance(raw_h, dict):
                    headers = {str(k): str(v) for k, v in raw_h.items()}
            except Exception:
                headers = None
            body = None
            try:
                body = getattr(exc, "body", None)
                if body is not None and not isinstance(body, str):
                    body = json.dumps(body)
            except Exception:
                body = None
            msg = str(exc)
            overflow = status == 400 and any(
                p in msg.lower()
                for p in ("context_length", "maximum context", "too many tokens", "token limit")
            )
            raise ProviderError(
                f"openai error: {exc}",
                status_code=status,
                response_headers=headers,
                response_body=body if isinstance(body, str) else None,
                is_retryable=bool(status and (status >= 500 or status == 429)),
                is_overflow=overflow,
            ) from exc
        except Exception as exc:
            raise ProviderError(f"openai stream failed: {exc}") from exc


def _attachments_to_content(attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for att in attachments:
        mime = (att.get("mime") or "").lower()
        url = att.get("url") or ""
        if not url:
            continue
        if mime.startswith("image/") or mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            out.append({"type": "image_url", "image_url": {"url": url}})
        elif mime == "application/pdf" or url.startswith("data:application/pdf"):
            # Chat Completions file support varies; send as text note + data URL hint
            out.append({
                "type": "text",
                "text": f"[attached PDF: {mime}] {url[:64]}...",
            })
        else:
            out.append({"type": "text", "text": f"[attached file: {mime}]"})
    return out


def _file_blocks_to_content(files: List[FileBlock]) -> List[Dict[str, Any]]:
    return _attachments_to_content([{"mime": f.mime, "url": f.url} for f in files])


def _looks_multimodal(msg: Message) -> bool:
    return any(isinstance(b, FileBlock) for b in msg.content)
