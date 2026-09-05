"""SSE streaming helpers for HTTP message turns.

Session already calls Renderer.on_text / on_tool_* during the agent loop.
StreamingRenderer pushes those into a thread-safe queue for SSE framing.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any, Dict, Iterator, Optional

from ..tools.base import ToolResult

# Sentinel placed on the queue when the turn is finished (or failed).
_DONE = object()

_DEFAULT_ARGS_CHARS = 500
_DEFAULT_OUTPUT_CHARS = 800


def _truncate(text: str, max_chars: int) -> str:
    one = str(text or "")
    if len(one) <= max_chars:
        return one
    if max_chars <= 3:
        return one[:max_chars]
    return one[: max_chars - 3] + "..."


def sse_pack(payload: Dict[str, Any]) -> bytes:
    """Encode one SSE `data:` frame (UTF-8)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class StreamingRenderer:
    """Renderer that enqueues JSON-serializable events for SSE."""

    def __init__(
        self,
        *,
        session_id: str = "",
        args_max_chars: int = _DEFAULT_ARGS_CHARS,
        output_max_chars: int = _DEFAULT_OUTPUT_CHARS,
    ):
        self._q: queue.Queue = queue.Queue()
        self._session_id = session_id or ""
        self._args_max = args_max_chars
        self._output_max = output_max_chars
        self._closed = False

    def _put(self, event: Dict[str, Any]) -> None:
        if self._closed:
            return
        if self._session_id and "session_id" not in event:
            event = {**event, "session_id": self._session_id}
        self._q.put(event)

    def close(self) -> None:
        """Signal the SSE consumer that no more events will arrive."""
        if self._closed:
            return
        self._closed = True
        self._q.put(_DONE)

    def on_text(self, text: str, **kwargs) -> None:
        if text:
            event: Dict[str, Any] = {"type": "text", "delta": text}
            first_token_at = kwargs.get("first_token_at")
            if first_token_at is not None:
                event["first_token_at"] = int(first_token_at)
            self._put(event)

    def on_reasoning(self, text: str, **kwargs) -> None:
        if text:
            event: Dict[str, Any] = {"type": "reasoning", "delta": text}
            first_token_at = kwargs.get("first_token_at")
            if first_token_at is not None:
                event["first_token_at"] = int(first_token_at)
            self._put(event)

    def on_tool_start(self, name: str, args: dict, **kwargs) -> None:
        raw = json.dumps(args or {}, ensure_ascii=False, default=str)
        event: Dict[str, Any] = {
            "type": "tool_start",
            "name": name,
            "args_preview": _truncate(raw, self._args_max),
        }
        call_id = kwargs.get("call_id")
        if call_id:
            event["id"] = str(call_id)
        if kwargs.get("step") is not None:
            event["step"] = int(kwargs["step"])
        if kwargs.get("started_at") is not None:
            event["started_at"] = int(kwargs["started_at"])
        self._put(event)

    def on_tool_result(self, name: str, result: ToolResult, **kwargs) -> None:
        out = getattr(result, "output", "") or ""
        event: Dict[str, Any] = {
            "type": "tool_result",
            "name": name,
            "is_error": bool(getattr(result, "is_error", False)),
            "output_preview": _truncate(str(out), self._output_max),
        }
        call_id = kwargs.get("call_id")
        if call_id:
            event["id"] = str(call_id)
        if kwargs.get("step") is not None:
            event["step"] = int(kwargs["step"])
        if kwargs.get("started_at") is not None:
            event["started_at"] = int(kwargs["started_at"])
        if kwargs.get("duration_ms") is not None:
            event["duration_ms"] = int(kwargs["duration_ms"])
        if kwargs.get("ended_at") is not None:
            event["ended_at"] = int(kwargs["ended_at"])
        self._put(event)

    def on_step(self, step: int, max_steps: int, **kwargs) -> None:
        event: Dict[str, Any] = {
            "type": "step",
            "step": int(step),
            "max_steps": int(max_steps),
        }
        if kwargs.get("started_at") is not None:
            event["started_at"] = int(kwargs["started_at"])
        self._put(event)

    def on_stop(self, reason: str, usage: dict, **kwargs) -> None:
        event: Dict[str, Any] = {
            "type": "stop",
            "reason": reason or "",
            "usage": dict(usage or {}),
        }
        if kwargs.get("step") is not None:
            event["step"] = int(kwargs["step"])
        if kwargs.get("started_at") is not None:
            event["started_at"] = int(kwargs["started_at"])
        if kwargs.get("first_token_at") is not None:
            event["first_token_at"] = int(kwargs["first_token_at"])
        if kwargs.get("completed_at") is not None:
            event["completed_at"] = int(kwargs["completed_at"])
        if kwargs.get("duration_ms") is not None:
            event["duration_ms"] = int(kwargs["duration_ms"])
        self._put(event)

    def on_error(self, message: str, **kwargs) -> None:
        event: Dict[str, Any] = {"type": "error", "message": str(message)}
        if kwargs.get("code"):
            event["code"] = str(kwargs["code"])
        if kwargs.get("msg"):
            event["msg"] = str(kwargs["msg"])
        self._put(event)

    def on_retry(self, attempt: int, message: str, wait: float) -> None:
        self._put(
            {
                "type": "retry",
                "attempt": int(attempt),
                "message": str(message),
                "wait": float(wait),
            }
        )

    def on_ack(self, **kwargs) -> None:
        event = {"type": str(kwargs.pop("type", None) or "ack")}
        event.update({k: v for k, v in kwargs.items() if v is not None})
        self._put(event)

    def on_progress(self, **kwargs) -> None:
        event = {"type": str(kwargs.pop("type", None) or "progress")}
        event.update({k: v for k, v in kwargs.items() if v is not None})
        if "stage" not in event:
            return
        self._put(event)

    def get_event(self, *, timeout: float = 0.4) -> Optional[Dict[str, Any]]:
        """Pop next event. Returns None when closed; ``{"type":"_poll"}`` on timeout."""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return {"type": "_poll"}
        if item is _DONE:
            return None
        return item

    def iter_events(self, *, timeout: float = 0.5) -> Iterator[Dict[str, Any]]:
        """Yield events until close(); poll with timeout so callers can check disconnect."""
        while True:
            event = self.get_event(timeout=timeout)
            if event is None:
                return
            yield event


def run_prompt_in_thread(sess: Any, prompt: str, renderer: StreamingRenderer) -> threading.Thread:
    """Start sess.prompt in a daemon thread; always close the renderer afterward."""

    def _target() -> None:
        try:
            sess.prompt(prompt)
        except Exception as exc:  # noqa: BLE001 — surface to SSE
            try:
                code = getattr(exc, "code", None)
                msg = getattr(exc, "msg", None) or str(exc)
                renderer.on_error(str(exc), code=code, msg=msg)
            except Exception:
                pass
        finally:
            renderer.close()

    t = threading.Thread(target=_target, name="sleuth-sse-prompt", daemon=True)
    t.start()
    return t
