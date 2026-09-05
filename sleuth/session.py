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
from typing import Any, List, Optional, Protocol

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
from .privacy import maybe_desensitize
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
    def on_text(self, text: str, **kwargs) -> None: ...
    def on_reasoning(self, text: str, **kwargs) -> None: ...
    def on_tool_start(self, name: str, args: dict, **kwargs) -> None: ...
    def on_tool_result(self, name: str, result: ToolResult, **kwargs) -> None: ...
    def on_step(self, step: int, max_steps: int, **kwargs) -> None: ...
    def on_stop(self, reason: str, usage: dict, **kwargs) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_retry(self, attempt: int, message: str, wait: float) -> None: ...


class NullRenderer:
    """Silent renderer; used by tests and the `--print` JSON mode."""

    def on_text(self, text: str, **kwargs) -> None: pass
    def on_reasoning(self, text: str, **kwargs) -> None: pass
    def on_tool_start(self, name: str, args: dict, **kwargs) -> None: pass
    def on_tool_result(self, name: str, result: ToolResult, **kwargs) -> None: pass
    def on_step(self, step: int, max_steps: int, **kwargs) -> None: pass
    def on_stop(self, reason: str, usage: dict, **kwargs) -> None: pass
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
    user_id: str = "local"
    role_id: Optional[str] = None
    org_id: Optional[str] = None
    yolo: bool = False  # auto-approve tools; CLI default False, server often True
    skill_names: List[str] = field(default_factory=list)  # pinned skills; default agent only
    # Session-file mailbox: which ready files apply this turn (None = all).
    _prompt_file_ids: Optional[List[str]] = None
    _turn_file_ids: List[str] = field(default_factory=list)
    # Citation sources harvested from tool JSON ``sources[]`` this turn.
    _turn_sources: List[dict] = field(default_factory=list)
    # HTTP parks `question` instead of blocking on stdin.
    block_on_question: bool = True
    _pending_ask: Optional[dict] = None

    # lifecycle
    status: str = "idle"  # "idle" | "busy" | "retry"
    _abort: threading.Event = field(default_factory=threading.Event)
    _last_usage: dict = field(default_factory=dict)
    _session_cost: float = 0.0
    _tokens: dict = field(default_factory=lambda: {
        "input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
    })

    # ---- public API ----

    def model_ref(self) -> str:
        """Current ``provider/model`` ref for display and persistence."""
        if self.config.model:
            return self.config.model
        pid = getattr(self.provider, "id", "") or "openai"
        return f"{pid}/{self.model_id}" if self.model_id else pid

    def set_model(self, ref_or_alias: str) -> str:
        """Switch provider/model for subsequent turns.

        ``ref_or_alias`` may be:

        - a ``SLEUTH_MODELS`` key (string ref or credential object)
        - a full ``provider/model`` ref
        - a bare model id (defaults provider to ``openai`` unless catalogued)

        Returns the resolved full ref.
        """
        from .config import parse_model_ref
        from .provider.factory import build_provider

        raw = (ref_or_alias or "").strip()
        if not raw:
            raise ValueError("model ref required (catalog key or provider/model)")
        # Remember catalog key when caller used an alias (for sticky restore).
        catalog_key = raw if raw in (self.config.models or {}) else ""
        ref = self.config.prepare_model_ref(raw)
        provider_id, model_id = parse_model_ref(ref)
        if not model_id:
            raise ValueError(f"invalid model ref: {ref!r}")
        self.config.model = ref
        self.provider = build_provider(self.config, provider_id)
        self.model_id = model_id
        self._model_catalog_key = catalog_key or getattr(self, "_model_catalog_key", "") or ""
        try:
            self._update_record()
        except Exception as exc:
            self.renderer.on_error(f"persist model failed: {exc}")
        return ref

    def set_agent(self, name: str, *, yolo: Optional[bool] = None) -> str:
        """Switch agent for subsequent turns and persist."""
        from .app import build_permission

        agent = self.config.resolve_agent_name(name)
        if not agent:
            raise ValueError("agent name required")
        from .memory.acl import assert_resource_allowed

        assert_resource_allowed(self.config, self.user_id, "agent", agent)
        if yolo is not None:
            self.yolo = bool(yolo)
        self.agent_name = agent
        self.permission = build_permission(self.config, agent, yolo=self.yolo)
        if not self.is_default_agent():
            self.skill_names = []
        mgr = getattr(self, "_mcp_manager", None)
        if mgr is not None:
            from .app import _bind_session_mcp

            _bind_session_mcp(self, mgr)
        try:
            self._update_record()
        except Exception as exc:
            self.renderer.on_error(f"persist agent failed: {exc}")
        return agent

    def is_default_agent(self) -> bool:
        default = (getattr(self.config, "default_agent", None) or "build").strip()
        return (self.agent_name or "").strip() == default

    def reset_model(self) -> str:
        """Restore the current agent's default model (not the sticky override)."""
        import os

        from .config import parse_model_ref
        from .provider.factory import build_provider

        agent = self.config.agent(self.agent_name)
        raw = agent.model or os.environ.get("SLEUTH_MODEL") or os.environ.get("OPENCODE_MODEL")
        if not raw:
            raise ValueError("no default model configured")
        ref = self.config.prepare_model_ref(str(raw))
        provider_id, model_id = parse_model_ref(ref)
        if not model_id:
            raise ValueError(f"invalid model ref: {ref!r}")
        self.config.model = ref
        self.provider = build_provider(self.config, provider_id)
        self.model_id = model_id
        self._model_catalog_key = ""
        try:
            self._update_record()
        except Exception as exc:
            self.renderer.on_error(f"persist model failed: {exc}")
        return ref

    @property
    def skill_name(self) -> Optional[str]:
        """First pinned skill, or None. Kept for older callers."""
        names = self.skill_names or []
        return names[0] if names else None

    @skill_name.setter
    def skill_name(self, value: Optional[str]) -> None:
        if isinstance(value, (list, tuple)):
            self.skill_names = [str(x).strip() for x in value if str(x).strip()]
            return
        raw = (value or "").strip() if isinstance(value, str) else ""
        self.skill_names = [raw] if raw else []

    def set_skills(self, names: Optional[List[str]]) -> List[str]:
        """Replace the pinned skill list. Empty clears. Requires the default agent when non-empty."""
        from .session_select import parse_skill_names

        parsed = parse_skill_names(names)
        if not parsed:
            self.skill_names = []
            try:
                self._update_record()
            except Exception as exc:
                self.renderer.on_error(f"persist skill failed: {exc}")
            return []
        if not self.is_default_agent():
            raise ValueError("skill only allowed when agent is the default agent")
        from .skill import get_skill, get_skills

        resolved: List[str] = []
        seen = set()
        for raw in parsed:
            info = get_skill(raw)
            if info is None:
                available = ", ".join(sorted(get_skills())) or "none"
                raise ValueError(f"unknown skill: {raw!r}. available: {available}")
            if info.name in seen:
                continue
            seen.add(info.name)
            if (getattr(info, "owner_agent", None) or "").strip():
                raise ValueError(
                    f"skill {info.name!r} is not pinnable (private to an agent)"
                )
            resolved.append(info.name)
        from .memory.acl import assert_resource_allowed

        for skill_name in resolved:
            assert_resource_allowed(self.config, self.user_id, "skill", skill_name)
        self.skill_names = resolved
        try:
            self._update_record()
        except Exception as exc:
            self.renderer.on_error(f"persist skill failed: {exc}")
        return list(self.skill_names)

    def set_skill(self, name: Optional[str]) -> Optional[str]:
        """Pin a single skill or clear. Non-empty pins require the default agent."""
        raw = (name or "").strip()
        if raw.lower() in ("", "off", "none", "default"):
            self.set_skills([])
            return None
        names = self.set_skills([raw])
        return names[0] if names else None

    def add_skill(self, name: str) -> List[str]:
        """Append a skill to the pin list (dedup, keep existing order)."""
        extra = (name or "").strip()
        return self.set_skills(list(self.skill_names or []) + [extra])

    def remove_skill(self, name: str) -> List[str]:
        """Drop a pinned skill by name. Missing names are ignored."""
        raw = (name or "").strip()
        remaining = [n for n in (self.skill_names or []) if n != raw]
        self.skill_names = remaining
        try:
            self._update_record()
        except Exception as exc:
            self.renderer.on_error(f"persist skill failed: {exc}")
        return list(self.skill_names)

    def set_yolo(self, enabled: bool) -> bool:
        """Toggle auto-approve and rebuild permission for the current agent."""
        from .app import build_permission

        self.yolo = bool(enabled)
        self.permission = build_permission(
            self.config, self.agent_name, yolo=self.yolo
        )
        return self.yolo

    def _model_payload(self) -> dict:
        pid = getattr(self.provider, "id", "") or ""
        payload = {
            "id": self.model_id,
            "providerID": pid,
            "ref": self.model_ref(),
        }
        key = getattr(self, "_model_catalog_key", "") or ""
        if key:
            payload["key"] = key
        return payload

    def prompt(self, user_text: str) -> str:
        """Send a user message and run the loop to completion.

        Returns the assistant's final text response.
        """
        self._abort.clear()
        # Lazy TTL skill refresh once per user turn; catalog stays frozen for this loop.
        from .skill import ensure_skills_fresh
        from .app import sync_session_mcp

        ensure_skills_fresh(self.config, self.workdir)
        sync_session_mcp(self)
        self._turn_file_ids = []
        self._turn_sources = []
        self._ensure_persisted()
        from .trace import now_ms

        pending = dict(self._pending_ask) if self._pending_ask else None
        if pending:
            self._flush_pending_ask(user_text, pending)

        user_msg = Message.user_text(
            user_text, agent=self.agent_name, started_at=now_ms()
        )
        self.messages.append(user_msg)
        self._persist_message(user_msg)
        self._maybe_title()
        try:
            self._run_loop()
        finally:
            self.status = "awaiting_user" if self._pending_ask else "idle"
            self._update_record()
        return self.last_assistant_text()

    def ask_payload(self) -> dict:
        """HTTP `done` extras: ok, or awaiting_user plus parked questions."""
        pending = self._pending_ask
        if not pending:
            return {"status": "ok"}
        return {
            "status": "awaiting_user",
            "questions": list(pending.get("questions") or []),
        }

    def cancel(self) -> None:
        """Abort the running turn at the next stream chunk.

        A parked ``question`` (HTTP ``awaiting_user``) is not cancelled here;
        the next user message resumes it.
        """
        self._abort.set()

    def is_busy(self) -> bool:
        return self.status == "busy"

    def last_assistant_text(self) -> str:
        for m in reversed(self.messages):
            if m.role == "assistant":
                text = self._scrub(m.text)
                if text.strip():
                    return text
                if self._pending_ask:
                    return self._scrub(self.ask_prompt_text())
                return text
        if self._pending_ask:
            return self._scrub(self.ask_prompt_text())
        return ""

    def ask_prompt_text(self) -> str:
        """Fallback user-visible copy when the model parked a question with no prose."""
        pending = self._pending_ask or {}
        questions = list(pending.get("questions") or [])
        lines = [
            "当前分析还缺少部分信息。请确认是否还有其他要补充的内容；"
            "有请直接提供，没有请回复继续，将按现有信息往下分析。"
        ]
        for q in questions:
            if not isinstance(q, dict):
                continue
            body = str(q.get("question") or "").strip()
            if body:
                lines.append(body)
        return "\n".join(lines)

    def _desensitize_on(self) -> bool:
        return bool(getattr(self.config, "output_desensitize", True))

    def _scrub(self, text: str) -> str:
        return maybe_desensitize(
            text,
            enabled=self._desensitize_on(),
            privacy=getattr(self.config, "privacy", None),
        )

    def _scrub_tool_result(self, result: ToolResult) -> ToolResult:
        if not self._desensitize_on():
            return result
        return ToolResult(
            title=self._scrub(result.title or ""),
            output=self._scrub(result.output or ""),
            metadata=dict(result.metadata or {}),
            is_error=result.is_error,
            attachments=list(result.attachments or []),
        )

    def _harvest_turn_sources(self, result: ToolResult) -> None:
        if result.is_error:
            return
        from .sources import collect_sources, merge_sources

        found = collect_sources(output=result.output, metadata=result.metadata)
        if found:
            self._turn_sources = merge_sources(self._turn_sources, found)

    def _append_sources_footer(self, text: str) -> str:
        from .sources import format_sources_footer

        footer = format_sources_footer(self._turn_sources, existing_text=text)
        if not footer:
            return text
        footer = self._scrub(footer)
        self.renderer.on_text(footer)
        return (text or "") + footer

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

    def _pinned_skill_prompt(self) -> str:
        if not self.skill_names or not self.is_default_agent():
            return ""
        from .tools.skill_tool import pinned_skills_system_block

        return pinned_skills_system_block(self.skill_names, session=self)

    def _bound_skill_names(self) -> List[str]:
        """Skill names auto-injected for a non-default agent (card/config + private)."""
        if self.is_default_agent():
            return []
        from .skill import get_skill, get_skills
        from .memory.acl import resource_allowed

        cfg = self.config
        user_id = self.user_id or ""
        canon = cfg.resolve_agent_name(self.agent_name)
        agent = cfg.agent(self.agent_name)
        names: List[str] = list(agent.skill_names or [])
        seen = set(names)
        for sk in get_skills().values():
            owner = (getattr(sk, "owner_agent", None) or "").strip()
            if not owner:
                continue
            if cfg.resolve_agent_name(owner) != canon:
                continue
            if sk.name not in seen:
                names.append(sk.name)
                seen.add(sk.name)
        allowed: List[str] = []
        for name in names:
            info = get_skill(name)
            if info is None:
                continue
            owner = (getattr(info, "owner_agent", None) or "").strip()
            if owner:
                if cfg.resolve_agent_name(owner) != canon:
                    continue
                if not resource_allowed(cfg, user_id, "agent", canon):
                    continue
            elif not resource_allowed(cfg, user_id, "skill", name):
                continue
            allowed.append(name)
        return allowed

    def _agent_skill_prompt(self) -> str:
        names = self._bound_skill_names()
        if not names:
            return ""
        from .tools.skill_tool import pinned_skills_system_block

        return pinned_skills_system_block(names, session=self)

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
            if self.store is not None and hasattr(self.store, "replace_messages"):
                try:
                    self.store.replace_messages(self.id, new_msgs)
                except Exception as exc:
                    self.renderer.on_error(f"compact persist failed: {exc}")
            on_retry = getattr(self.renderer, "on_retry", None)
            if callable(on_retry):
                on_retry(0, "context compacted", 0.0)

    # ---- the loop ----

    def _run_loop(self) -> None:
        agent_cfg = self.config.agent(self.agent_name)
        max_steps = self.config.max_steps or agent_cfg.steps
        tools = self.registry.specs(permission_rules=self.permission.rules)
        system = assemble(
            workdir=self.workdir,
            config=self.config,
            agent_name=self.agent_name,
            model=self.model_id,
            tool_specs=tools,
            guardrails=self.config.guardrails,
            session=self,
        )
        pinned = self._pinned_skill_prompt()
        if pinned:
            system = system + "\n\n" + pinned
        bound = self._agent_skill_prompt()
        if bound:
            system = system + "\n\n" + bound
        from .files.ingest import ensure_session_excerpts
        from .files.mailbox import files_prompt_block

        wait_s = float(getattr(getattr(self.config, "files", None), "prompt_wait_s", 8) or 0)
        ensure_session_excerpts(self, timeout_s=wait_s)
        files_block = files_prompt_block(self)
        if files_block:
            system = system + "\n\n" + files_block
        from .memory.prompt import memory_prompt_block

        mem_block = memory_prompt_block(self)
        if mem_block:
            system = system + "\n\n" + mem_block

        from .trace import now_ms

        for step in range(1, max_steps + 1):
            step_mark_at = now_ms()
            self.renderer.on_step(step, max_steps, started_at=step_mark_at)
            self.status = "busy"
            self._update_record()
            self._maybe_compact()

            start_snap = self._safe_capture()

            text_buf: List[str] = []
            reasoning_buf: List[str] = []
            tool_uses: List[ToolUseBlock] = []
            stop_reason = "end_turn"
            usage: dict = {}
            stream_started_at = step_mark_at
            first_token_at: Optional[int] = None

            aborted = False
            stream_error: Optional[ProviderError] = None

            for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
                text_buf.clear()
                reasoning_buf.clear()
                tool_uses.clear()
                usage = {}
                aborted = False
                stream_started_at = now_ms()
                first_token_at = None
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
                            chunk = self._scrub(event.text)
                            reasoning_buf.append(chunk)
                            if first_token_at is None:
                                first_token_at = now_ms()
                                self.renderer.on_reasoning(
                                    chunk, first_token_at=first_token_at
                                )
                            else:
                                self.renderer.on_reasoning(chunk)
                        elif isinstance(event, TextDelta):
                            chunk = self._scrub(event.text)
                            text_buf.append(chunk)
                            if first_token_at is None:
                                first_token_at = now_ms()
                                self.renderer.on_text(
                                    chunk, first_token_at=first_token_at
                                )
                            else:
                                self.renderer.on_text(chunk)
                        elif isinstance(event, ToolUse):
                            block = ToolUseBlock(id=event.id, name=event.name, input=event.input)
                            tool_uses.append(block)
                            self.renderer.on_tool_start(
                                event.name,
                                event.input,
                                call_id=event.id,
                                step=step,
                                started_at=now_ms(),
                            )
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
                        self.renderer.on_error(self._scrub(f"retry {attempt}: {why} (wait {wait:.1f}s)"))
                    if not sleep_interruptible(wait, self._abort):
                        aborted = True
                        stream_error = None
                        break
                    self.status = "busy"
                    self._update_record()

            completed_at = now_ms()
            duration_ms = max(0, completed_at - stream_started_at)
            stop_kwargs = dict(
                step=step,
                started_at=stream_started_at,
                first_token_at=first_token_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )

            if stream_error is not None:
                err_text = self._scrub(str(stream_error))
                self.renderer.on_error(err_text)
                err_msg = Message.assistant(
                    [TextBlock(f"[error] {err_text}")], error=err_text,
                    agent=self.agent_name, model=self.model_id,
                    step=step,
                    started_at=stream_started_at,
                    first_token_at=first_token_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                )
                self.messages.append(err_msg)
                self._persist_message(err_msg)
                return

            end_snap = self._safe_capture()

            assistant_blocks: List = []
            if reasoning_buf:
                assistant_blocks.append(ReasoningBlock(self._scrub("".join(reasoning_buf))))
            text_out = self._scrub("".join(text_buf)) if text_buf else ""
            if (not tool_uses) and (not aborted):
                text_out = self._append_sources_footer(text_out)
            if text_out:
                assistant_blocks.append(TextBlock(text_out))
            assistant_blocks.extend(tool_uses)

            assistant_msg = Message.assistant(
                assistant_blocks,
                model=self.model_id, agent=self.agent_name,
                snapshots={"start": start_snap, "end": end_snap},
                usage=usage, aborted=aborted,
                cost=compute_cost(usage, self._cost_rates()),
                step=step,
                started_at=stream_started_at,
                first_token_at=first_token_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
            self.messages.append(assistant_msg)
            self._persist_message(assistant_msg)
            self._accumulate_usage(
                usage, message_id=str(assistant_msg.metadata.get("id") or "")
            )
            self._update_record()

            if aborted:
                self.renderer.on_stop("aborted", usage, **stop_kwargs)
                return

            # Continue only when the model requested tools (finish_reason tool_calls)
            if not tool_uses:
                self.renderer.on_stop(stop_reason, usage, **stop_kwargs)
                return
            if stop_reason in ("end_turn", "stop", "max_tokens", "length") and not tool_uses:
                self.renderer.on_stop(stop_reason, usage, **stop_kwargs)
                return
            if step >= max_steps:
                self.renderer.on_stop("max_steps", usage, **stop_kwargs)
                note = Message.user_text("[system] maximum steps reached; stopping.")
                self.messages.append(note)
                self._persist_message(note)
                return

            will_park = any(
                tu.name == "question" and not self.block_on_question for tu in tool_uses
            )
            if not will_park:
                self.renderer.on_stop(stop_reason, usage, **stop_kwargs)

            results = []
            tool_spans: List[dict] = []
            parked_items: List[dict] = []
            sibling_results: List[dict] = []
            sibling_spans: List[dict] = []
            for tu in tool_uses:
                exec_started = now_ms()
                if tu.name == "question" and not self.block_on_question:
                    from .tools.question import parse_question_prompts

                    try:
                        questions = parse_question_prompts(tu.input or {})
                    except Exception:
                        questions = []
                    parked_items.append(
                        {"tool_use_id": tu.id, "questions": questions}
                    )
                    continue
                result = self._scrub_tool_result(self._execute_tool(tu))
                self._harvest_turn_sources(result)
                ended_at = now_ms()
                span_ms = max(0, ended_at - exec_started)
                span = {
                    "id": tu.id,
                    "name": tu.name,
                    "started_at": exec_started,
                    "duration_ms": span_ms,
                    "ended_at": ended_at,
                    "is_error": bool(result.is_error),
                }
                tool_spans.append(span)
                block = ToolResultBlock(
                    tool_use_id=tu.id,
                    content=result.output,
                    is_error=result.is_error,
                    attachments=list(result.attachments or []),
                )
                results.append(block)
                sibling_results.append(
                    {
                        "tool_use_id": tu.id,
                        "content": result.output,
                        "is_error": bool(result.is_error),
                        "attachments": list(result.attachments or []),
                    }
                )
                sibling_spans.append(span)
                self.renderer.on_tool_result(
                    tu.name,
                    result,
                    call_id=tu.id,
                    step=step,
                    started_at=exec_started,
                    duration_ms=span_ms,
                    ended_at=ended_at,
                )

            if parked_items:
                questions: List[Any] = []
                for item in parked_items:
                    questions.extend(item.get("questions") or [])
                self._pending_ask = {
                    "items": parked_items,
                    "questions": questions,
                    "sibling_results": sibling_results,
                    "sibling_spans": sibling_spans,
                }
                self.status = "awaiting_user"
                self._update_record()
                self.renderer.on_stop("ask", usage, **stop_kwargs)
                return

            tool_msg = Message.tool_results(results, tool_spans=tool_spans)
            self.messages.append(tool_msg)
            self._persist_message(tool_msg)

    def _flush_pending_ask(self, user_text: str, pending: dict) -> None:
        """Turn the next user message into tool_result(s) for a parked question."""
        from .tools.question import format_question_result
        from .trace import now_ms

        items = list(pending.get("items") or [])
        if not items and pending.get("tool_use_id"):
            items = [
                {
                    "tool_use_id": pending.get("tool_use_id"),
                    "questions": pending.get("questions") or [],
                }
            ]
        answer_text = (user_text or "").strip()
        blocks: List[ToolResultBlock] = []
        spans: List[dict] = []
        for raw in pending.get("sibling_results") or []:
            if not isinstance(raw, dict):
                continue
            blocks.append(
                ToolResultBlock(
                    tool_use_id=str(raw.get("tool_use_id") or ""),
                    content=str(raw.get("content") or ""),
                    is_error=bool(raw.get("is_error")),
                    attachments=list(raw.get("attachments") or []),
                )
            )
        spans.extend(
            s for s in (pending.get("sibling_spans") or []) if isinstance(s, dict)
        )
        started = now_ms()
        for item in items:
            questions = list(item.get("questions") or [])
            answers = [[answer_text] for _ in questions] or [[answer_text]]
            output = format_question_result(questions, answers)
            tu_id = str(item.get("tool_use_id") or "")
            blocks.append(
                ToolResultBlock(
                    tool_use_id=tu_id,
                    content=output,
                    is_error=False,
                )
            )
            ended = now_ms()
            spans.append(
                {
                    "id": tu_id,
                    "name": "question",
                    "started_at": started,
                    "duration_ms": max(0, ended - started),
                    "ended_at": ended,
                    "is_error": False,
                }
            )
            self.renderer.on_tool_result(
                "question",
                ToolResult.success("question", output, answers=answers),
                call_id=tu_id,
                started_at=started,
                duration_ms=max(0, ended - started),
                ended_at=ended,
            )
        if blocks:
            tool_msg = Message.tool_results(blocks, tool_spans=spans)
            self.messages.append(tool_msg)
            self._persist_message(tool_msg)
        self._pending_ask = None
        self.status = "busy"
        self._update_record()

    def _execute_tool(self, tool_use: ToolUseBlock) -> ToolResult:
        return self.execute_guarded_tool(tool_use.name, tool_use.input or {})

    def execute_guarded_tool(self, tool_name: str, args: dict) -> ToolResult:
        """Run a registry tool through permission + MCP bridge (orchestration entry point)."""
        ctx = ToolContext(
            workdir=self.workdir,
            session_id=self.id,
            agent=self.agent_name,
            permission=self.permission,
            abort=self._abort,
            session=self,
            guardrails_enabled=bool(getattr(self.config, "guardrails", True)),
        )
        renderer = getattr(self, "renderer", None)
        if renderer is not None and hasattr(renderer, "on_tool_start"):
            try:
                renderer.on_tool_start(tool_name, args or {})
            except Exception:
                pass
        try:
            if self._abort.is_set():
                return ToolResult.error(tool_name, "aborted before execution")
            result = self.registry.execute(tool_name, args or {}, ctx)
        except PermissionDenied as exc:
            result = ToolResult.error(tool_name, f"permission denied: {exc}")
        if renderer is not None and hasattr(renderer, "on_tool_result"):
            try:
                renderer.on_tool_result(tool_name, result)
            except Exception:
                pass
        return result

    def _cost_rates(self) -> Optional[dict]:
        pid = getattr(self.provider, "id", "") or ""
        options = self.config.provider_options(pid)
        cost = options.get("cost")
        return cost if isinstance(cost, dict) else None

    def _accumulate_usage(self, usage: dict, *, message_id: Optional[str] = None) -> None:
        if not usage:
            return
        self._last_usage = dict(usage)
        for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
            self._tokens[key] = self._tokens.get(key, 0) + int(usage.get(key, 0) or 0)
        turn_cost = compute_cost(usage, self._cost_rates())
        self._session_cost += turn_cost
        if self.store is not None and message_id and hasattr(self.store, "save_usage_event"):
            try:
                from .storage.base import UsageEvent

                self.store.save_usage_event(
                    UsageEvent(
                        user_id=self.user_id,
                        session_id=self.id,
                        message_id=message_id,
                        model=self.model_id,
                        tokens_input=int(usage.get("input", 0) or 0),
                        tokens_output=int(usage.get("output", 0) or 0),
                        tokens_reasoning=int(usage.get("reasoning", 0) or 0),
                        tokens_cache_read=int(usage.get("cache_read", 0) or 0),
                        tokens_cache_write=int(usage.get("cache_write", 0) or 0),
                        cost=turn_cost,
                    )
                )
            except Exception as exc:
                self.renderer.on_error(f"usage event persist failed: {exc}")

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
            self._write_skill_metadata(meta)
            rec = SessionRecord(
                id=self.id,
                directory=str(self.workdir),
                title=self.title,
                agent=self.agent_name,
                user_id=self.user_id or getattr(self.config, "user_id", "local") or "local",
                model=self._model_payload(),
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
        existing.user_id = self.user_id or existing.user_id or "local"
        existing.model = self._model_payload()
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
        self._write_skill_metadata(existing.metadata)
        cached = getattr(self, "_files", None)
        if cached is not None:
            existing.metadata["files"] = list(cached)
        if self._pending_ask:
            existing.metadata["pending_ask"] = dict(self._pending_ask)
        else:
            existing.metadata.pop("pending_ask", None)
        self.store.update_session(existing)

    def _write_skill_metadata(self, meta: dict) -> None:
        names = [n for n in (self.skill_names or []) if n]
        if names:
            meta["skills"] = names
            meta["skill"] = names[0]
        else:
            meta.pop("skills", None)
            meta.pop("skill", None)

    @classmethod
    def load(cls, *, provider, registry, config, workdir, permission, store,
             session_id_value, agent_name="build", model_id="", renderer=None,
             prefer_agent=None) -> "Session":
        """Resume a persisted session by id (port of opencode session load)."""
        from .config import parse_model_ref
        from .provider.factory import build_provider

        rec = store.get_session(session_id_value)
        messages = store.load_messages(session_id_value) if rec else []
        parent_id = None
        stored_skills: List[str] = []
        if rec and rec.metadata:
            from .session_select import skills_from_metadata

            parent_id = rec.metadata.get("parent_id")
            stored_skills = skills_from_metadata(rec.metadata)
        pending_ask = None
        if rec and rec.metadata and isinstance(rec.metadata.get("pending_ask"), dict):
            pending_ask = dict(rec.metadata.get("pending_ask") or {})
        user_id = (rec.user_id if rec else None) or getattr(config, "user_id", "local") or "local"

        catalog_key = ""
        if rec and rec.model:
            stored = rec.model
            catalog_key = str(stored.get("key") or "").strip()
            restore_raw = (
                catalog_key
                or str(stored.get("ref") or "").strip()
                or (
                    f"{stored.get('providerID')}/{stored.get('id')}"
                    if stored.get("providerID") and stored.get("id")
                    else ""
                )
                or str(stored.get("id") or "").strip()
            )
            if restore_raw:
                try:
                    ref = config.prepare_model_ref(restore_raw)
                    provider_id, mid = parse_model_ref(ref)
                    if mid:
                        provider = build_provider(config, provider_id)
                        config.model = ref
                        model_id = mid
                        if not catalog_key and restore_raw in (config.models or {}):
                            catalog_key = restore_raw
                except Exception:
                    pass

        if prefer_agent:
            resolved_agent = config.resolve_agent_name(prefer_agent)
        else:
            stored = (rec.agent if rec and rec.agent else agent_name) or agent_name
            resolved_agent = config.resolve_agent_name(stored)

        default_agent = (getattr(config, "default_agent", None) or "build").strip()
        if stored_skills and resolved_agent != default_agent:
            stored_skills = []

        sess = cls(
            provider=provider, registry=registry, config=config,
            workdir=workdir, permission=permission,
            agent_name=resolved_agent,
            model_id=model_id,
            id=session_id_value, messages=messages,
            renderer=renderer or NullRenderer(), store=store,
            title=(rec.title if rec else default_title()),
            parent_id=parent_id,
            user_id=user_id,
            skill_names=stored_skills,
        )
        sess._pending_ask = pending_ask
        sess._model_catalog_key = catalog_key
        if rec:
            sess._session_cost = float(rec.cost or 0)
            sess._tokens = {
                "input": rec.tokens_input or 0,
                "output": rec.tokens_output or 0,
                "reasoning": rec.tokens_reasoning or 0,
                "cache_read": rec.tokens_cache_read or 0,
                "cache_write": rec.tokens_cache_write or 0,
            }
        if store is not None and hasattr(store, "load_todos"):
            try:
                from .tools import todo as todo_mod

                todos = store.load_todos(session_id_value)
                todo_mod.set_state(todos)
            except Exception:
                pass
        return sess
