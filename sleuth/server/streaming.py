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

    def on_text(self, text: str) -> None:
        if text:
            self._put({"type": "text", "delta": text})

    def on_reasoning(self, text: str) -> None:
        if text:
            self._put({"type": "reasoning", "delta": text})

    def on_tool_start(self, name: str, args: dict) -> None:
        raw = json.dumps(args or {}, ensure_ascii=False, default=str)
        self._put(
            {
                "type": "tool_start",
                "name": name,
                "args_preview": _truncate(raw, self._args_max),
            }
        )

    def on_tool_result(self, name: str, result: ToolResult) -> None:
        out = getattr(result, "output", "") or ""
        self._put(
            {
                "type": "tool_result",
                "name": name,
                "is_error": bool(getattr(result, "is_error", False)),
                "output_preview": _truncate(str(out), self._output_max),
            }
        )

    def on_step(self, step: int, max_steps: int) -> None:
        self._put({"type": "step", "step": int(step), "max_steps": int(max_steps)})

    def on_stop(self, reason: str, usage: dict) -> None:
        self._put(
            {
                "type": "stop",
                "reason": reason or "",
                "usage": dict(usage or {}),
            }
        )

    def on_error(self, message: str) -> None:
        self._put({"type": "error", "message": str(message)})

    def on_retry(self, attempt: int, message: str, wait: float) -> None:
        self._put(
            {
                "type": "retry",
                "attempt": int(attempt),
                "message": str(message),
                "wait": float(wait),
            }
        )

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
                renderer.on_error(str(exc))
            except Exception:
                pass
        finally:
            renderer.close()

    t = threading.Thread(target=_target, name="sleuth-sse-prompt", daemon=True)
    t.start()
    return t
