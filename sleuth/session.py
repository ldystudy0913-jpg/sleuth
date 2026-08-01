"""Session and the agentic loop.

This is the heart of the port. opencode's loop (`session/prompt.ts`
`runLoop`) does:

    while True:
        create an empty assistant message
        assemble system prompt + tools + history
        stream the model, processing text/reasoning/tool/finish events
        if the assistant finished with no tool calls: break
        execute the requested tools, append tool_results
        continue

We replicate that control flow against our provider event protocol, and add
the persistence / snapshot / status / abort / retry / title / compaction
integration from opencode.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol

from .compaction import compact, is_overflow
from .config import Config, parse_model_ref
from .messages import (
    Message,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from .permission import Permission, PermissionDenied
from .provider.base import Provider, ProviderError, ReasoningDelta, Stop, TextDelta, ToolUse
from .provider.factory import build_provider
from .prompts import assemble
from .retry import DEFAULT_MAX_ATTEMPTS, delay as retry_delay, retryable, sleep_interruptible
from .title import default_title, ensure_title, resolve_title_model
from .tools.base import ToolContext, ToolResult
from .tools.registry import ToolRegistry
from .usage import compute_cost
from .util.ids import session_id


# ---------------------------------------------------------------------------
# renderer protocol — the UI surface the loop calls into
# ---------------------------------------------------------------------------


class Renderer(Protocol):
    def on_text(self, text: str) -> None: ...
    def on_reasoning(self, text: str) -> None: ...
    def on_tool_start(self, name: str, args: dict) -> None: ...
    def on_tool_result(self, name: str, result: ToolResult) -> None: ...
    def on_step(self, step: int, max_steps: int) -> None: ...
    def on_stop(self, reason: str, usage: dict) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_retry(self, attempt: int, message: str, wait: float) -> None: ...


class NullRenderer:
    """Silent renderer; used by tests and the `--print` JSON mode."""

    def on_text(self, text: str) -> None: pass
    def on_reasoning(self, text: str) -> None: pass
    def on_tool_start(self, name: str, args: dict) -> None: pass
    def on_tool_result(self, name: str, result: ToolResult) -> None: pass
    def on_step(self, step: int, max_steps: int) -> None: pass
    def on_stop(self, reason: str, usage: dict) -> None: pass
    def on_error(self, message: str) -> None: print(message, file=sys.stderr)
    def on_retry(self, attempt: int, message: str, wait: float) -> None: pass


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class Session:
    provider: Provider
    registry: ToolRegistry
    config: Config
    workdir: Path
    permission: Permission
    agent_name: str = "build"
    model_id: str = ""
    id: str = field(default_factory=session_id)
    messages: List[Message] = field(default_factory=list)
    renderer: Renderer = field(default_factory=NullRenderer)
    # optional persistence (None = in-memory only, like the original MVP)
    store: object = None  # Store protocol (sleuth.storage.base.Store)
    title: str = field(default_factory=default_title)
    parent_id: Optional[str] = None

    # lifecycle
    status: str = "idle"  # "idle" | "busy" | "retry"
    _abort: threading.Event = field(default_factory=threading.Event)
    _last_usage: dict = field(default_factory=dict)
    _session_cost: float = 0.0
    _tokens: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
    })

    # ---- public API ----

    def prompt(self, user_text: str) -> str:
        """Send a user message and run the loop to completion.

        Returns the assistant's final text response.
        """
        self._abort.clear()
        self._ensure_persisted()
        user_msg = Message.user_text(user_text, agent=self.agent_name)
        self.messages.append(user_msg)
        self._persist_message(user_msg)
        self._maybe_title()
        try:
            self._run_loop()
        finally:
            self.status = "idle"
            self._update_record()
        return self.last_assistant_text()

    def cancel(self) -> None:
        """Abort the running turn at the next stream chunk (port of
        SessionRunState.cancel). No mid-step pause/resume — opencode has none.
        """
        self._abort.set()

    def is_busy(self) -> bool:
        return self.status == "busy"

    def last_assistant_text(self) -> str:
        for m in reversed(self.messages):
            if m.role == "assistant":
                return m.text
        return ""

    # ---- title / compaction helpers ----

    def _small_provider_model(self) -> tuple:
        """Provider + model id for title/compaction (opencode getSmallModel)."""
        ref = self.config.small_model
        if ref:
            pid, mid = parse_model_ref(ref)
            try:
                return build_provider(self.config, pid), mid
            except Exception:
                pass
        return self.provider, resolve_title_model(self.config, self.model_id)

    def _maybe_title(self) -> None:
        if self.parent_id:
            return
        provider, model = self._small_provider_model()
        new_title = ensure_title(
            title=self.title,
            messages=self.messages,
            provider=provider,
            model=model,
            parent_id=self.parent_id,
        )
        if new_title:
            self.title = new_title
            self._update_record()

    def _maybe_compact(self) -> None:
        if not is_overflow(self.config, self._last_usage):
            return
        provider, model = self._small_provider_model()
        prev = None
        for m in self.messages:
            if m.metadata.get("compacted") and m.text:
                prev = m.text
        new_msgs = compact(
            messages=self.messages,
            provider=provider,
            model=model,
            previous_summary=prev,
        )
        if new_msgs is not None:
            self.messages = new_msgs
            on_retry = getattr(self.renderer, "on_retry", None)
            if callable(on_retry):
                on_retry(0, "context compacted", 0.0)

    # ---- the loop ----

    def _run_loop(self) -> None:
        agent_cfg = self.config.agent(self.agent_name)
        max_steps = self.config.max_steps or agent_cfg.steps
        system = assemble(
            workdir=self.workdir,
            config=self.config,
            agent_name=self.agent_name,
            model=self.model_id,
        )
        tools = self.registry.specs(permission_rules=self.permission.rules)

        for step in range(1, max_steps + 1):
            self.renderer.on_step(step, max_steps)
            self.status = "busy"
            self._update_record()
            self._maybe_compact()

            start_snap = self._safe_capture()

            text_buf: List[str] = []
            reasoning_buf: List[str] = []
            tool_uses: List[ToolUseBlock] = []
            stop_reason = "end_turn"
            usage: dict = {}

            aborted = False
            stream_error: Optional[ProviderError] = None

            for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
                text_buf.clear()
                reasoning_buf.clear()
                tool_uses.clear()
                usage = {}
                aborted = False
                try:
                    for event in self.provider.stream(
                        system=system,
                        messages=self.messages,
                        tools=tools,
                        model=self.model_id,
                    ):
                        if self._abort.is_set():
                            aborted = True
                            break
                        if isinstance(event, ReasoningDelta):
                            reasoning_buf.append(event.text)
                            self.renderer.on_reasoning(event.text)
                        elif isinstance(event, TextDelta):
                            text_buf.append(event.text)
                            self.renderer.on_text(event.text)
                        elif isinstance(event, ToolUse):
                            block = ToolUseBlock(id=event.id, name=event.name, input=event.input)
                            tool_uses.append(block)
                            self.renderer.on_tool_start(event.name, event.input)
                        elif isinstance(event, Stop):
                            stop_reason = event.reason
                            usage = event.usage or {}
                    stream_error = None
                    break
                except ProviderError as exc:
                    stream_error = exc
                    why = retryable(exc)
                    if why is None or attempt >= DEFAULT_MAX_ATTEMPTS:
                        break
                    wait = retry_delay(attempt, exc)
                    self.status = "retry"
                    self._update_record()
                    on_retry = getattr(self.renderer, "on_retry", None)
                    if callable(on_retry):
                        on_retry(attempt, why, wait)
                    else:
                        self.renderer.on_error(f"retry {attempt}: {why} (wait {wait:.1f}s)")
                    if not sleep_interruptible(wait, self._abort):
                        aborted = True
                        stream_error = None
                        break
                    self.status = "busy"
                    self._update_record()

            if stream_error is not None:
                self.renderer.on_error(str(stream_error))
                err_msg = Message.assistant(
                    [TextBlock(f"[error] {stream_error}")], error=str(stream_error),
                    agent=self.agent_name, model=self.model_id,
                )
                self.messages.append(err_msg)
                self._persist_message(err_msg)
                return

            end_snap = self._safe_capture()
            self._accumulate_usage(usage)

            assistant_blocks: List = []
            if reasoning_buf:
                assistant_blocks.append(ReasoningBlock("".join(reasoning_buf)))
            if text_buf:
                assistant_blocks.append(TextBlock("".join(text_buf)))
            assistant_blocks.extend(tool_uses)

            assistant_msg = Message.assistant(
                assistant_blocks,
                model=self.model_id, agent=self.agent_name,
                snapshots={"start": start_snap, "end": end_snap},
                usage=usage, aborted=aborted,
                cost=compute_cost(usage, self._cost_rates()),
            )
            self.messages.append(assistant_msg)
            self._persist_message(assistant_msg)
            self._update_record()

            if aborted:
                self.renderer.on_stop("aborted", usage)
                return

            # Continue only when the model requested tools (finish_reason tool_calls)
            if not tool_uses:
                self.renderer.on_stop(stop_reason, usage)
                return
            if stop_reason in ("end_turn", "stop", "max_tokens", "length") and not tool_uses:
                self.renderer.on_stop(stop_reason, usage)
                return
            if step >= max_steps:
                self.renderer.on_stop("max_steps", usage)
                note = Message.user_text("[system] maximum steps reached; stopping.")
                self.messages.append(note)
                self._persist_message(note)
                return

            results = []
            for tu in tool_uses:
                result = self._execute_tool(tu)
                results.append(
                    ToolResultBlock(
                        tool_use_id=tu.id,
                        content=result.output,
                        is_error=result.is_error,
                        attachments=list(result.attachments or []),
                    )
                )
                self.renderer.on_tool_result(tu.name, result)
            tool_msg = Message.tool_results(results)
            self.messages.append(tool_msg)
            self._persist_message(tool_msg)

    def _execute_tool(self, tool_use: ToolUseBlock) -> ToolResult:
        ctx = ToolContext(
            workdir=self.workdir,
            session_id=self.id,
            agent=self.agent_name,
            permission=self.permission,
            abort=self._abort,
            session=self,
        )
        try:
            if self._abort.is_set():
                return ToolResult.error(tool_use.name, "aborted before execution")
            return self.registry.execute(tool_use.name, tool_use.input, ctx)
        except PermissionDenied as exc:
            return ToolResult.error(tool_use.name, f"permission denied: {exc}")

    def _cost_rates(self) -> Optional[dict]:
        pid = getattr(self.provider, "id", "") or ""
        options = self.config.provider_options(pid)
        cost = options.get("cost")
        return cost if isinstance(cost, dict) else None

    def _accumulate_usage(self, usage: dict) -> None:
        if not usage:
            return
        self._last_usage = dict(usage)
        for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
            self._tokens[key] = self._tokens.get(key, 0) + int(usage.get(key, 0) or 0)
        self._session_cost += compute_cost(usage, self._cost_rates())

    # ---- snapshot ----

    def _safe_capture(self) -> Optional[str]:
        """Capture a working-tree snapshot, returning None on failure (e.g.
        not a git repo). The loop must never break because snapshots are
        unavailable."""
        try:
            from . import snapshot
            return snapshot.capture(self.workdir)
        except Exception:
            return None

    def revert_to(self, message_id: str) -> bool:
        """Restore the working tree to the snapshot captured at the given
        assistant message (opencode `revert`)."""
        from . import snapshot
        for m in self.messages:
            if m.metadata.get("id") == message_id and m.role == "assistant":
                snaps = m.metadata.get("snapshots") or {}
                tree = snaps.get("start") or snaps.get("end")
                if tree:
                    return snapshot.revert_to(self.workdir, tree)
        return False

    # ---- persistence ----

    def _ensure_persisted(self) -> None:
        if self.store is None:
            return
        from .storage.base import SessionRecord
        existing = self.store.get_session(self.id)
        if existing is None:
            meta = {"version": "0.1.0"}
            if self.parent_id:
                meta["parent_id"] = self.parent_id
            rec = SessionRecord(
                id=self.id,
                directory=str(self.workdir),
                title=self.title,
                agent=self.agent_name,
                model={"id": self.model_id, "providerID": getattr(self.provider, "id", "")},
                metadata=meta,
                permission=[r.__dict__ for r in self.permission.rules],
            )
            self.store.create_session(rec)

    def _persist_message(self, msg: Message) -> None:
        if self.store is None:
            return
        try:
            self.store.save_message(self.id, msg)
        except Exception as exc:  # persistence must never kill the loop
            self.renderer.on_error(f"persist failed: {exc}")

    def _update_record(self) -> None:
        if self.store is None:
            return
        from .storage.base import SessionRecord
        existing = self.store.get_session(self.id)
        if existing is None:
            self._ensure_persisted()
            existing = self.store.get_session(self.id)
            if existing is None:
                return
        existing.agent = self.agent_name
        existing.title = self.title
        existing.cost = self._session_cost
        existing.tokens_input = self._tokens.get("input", 0)
        existing.tokens_output = self._tokens.get("output", 0)
        existing.tokens_reasoning = self._tokens.get("reasoning", 0)
        existing.tokens_cache_read = self._tokens.get("cache_read", 0)
        existing.tokens_cache_write = self._tokens.get("cache_write", 0)
        existing.metadata = dict(existing.metadata or {})
        existing.metadata["status"] = self.status
        if self.parent_id:
            existing.metadata["parent_id"] = self.parent_id
        self.store.update_session(existing)

    @classmethod
    def load(cls, *, provider, registry, config, workdir, permission, store,
             session_id_value, agent_name="build", model_id="", renderer=None) -> "Session":
        """Resume a persisted session by id (port of opencode session load)."""
        rec = store.get_session(session_id_value)
        messages = store.load_messages(session_id_value) if rec else []
        parent_id = None
        if rec and rec.metadata:
            parent_id = rec.metadata.get("parent_id")
        sess = cls(
            provider=provider, registry=registry, config=config,
            workdir=workdir, permission=permission,
            agent_name=(rec.agent if rec else agent_name) or agent_name,
            model_id=(rec.model.get("id") if rec and rec.model else model_id) or model_id,
            id=session_id_value, messages=messages,
            renderer=renderer or NullRenderer(), store=store,
            title=(rec.title if rec else default_title()),
            parent_id=parent_id,
        )
        if rec:
            sess._session_cost = float(rec.cost or 0)
            sess._tokens = {
                "input": rec.tokens_input or 0,
                "output": rec.tokens_output or 0,
                "reasoning": rec.tokens_reasoning or 0,
                "cache_read": rec.tokens_cache_read or 0,
                "cache_write": rec.tokens_cache_write or 0,
            }
        return sess
