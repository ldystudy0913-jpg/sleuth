"""Provider protocol and streaming event types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Protocol, runtime_checkable

from ..messages import ContentBlock, Message, TextBlock


# ---------------------------------------------------------------------------
# events yielded by Provider.stream
# ---------------------------------------------------------------------------


@dataclass
class TextDelta:
    """A chunk of assistant text. Print it as it arrives."""

    text: str
    type: str = "text-delta"


@dataclass
class ReasoningDelta:
    """A chunk of the model's chain-of-thought / thinking.

    Mirrors opencode's `reasoning-delta` event (packages/llm/src/schema/
    events.ts). On the OpenAI-compatible path this comes from
    `delta.reasoning_content`. The session loop accumulates it into a
    ReasoningBlock and renders it dim/gray; it is NOT sent back to the model
    on subsequent turns for non-interleaved models.
    """

    text: str
    id: str = "reasoning-0"
    type: str = "reasoning-delta"


@dataclass
class ToolUse:
    """A completed tool call. We yield the *whole* call once the model has
    finished emitting its JSON arguments; the loop then executes it."""

    id: str
    name: str
    input: dict
    type: str = "tool-use"


@dataclass
class Stop:
    """The model finished this assistant turn.

    `reason` is provider-specific ("end_turn", "tool_use", "stop", ...).
    `usage` is token counts when available.
    """

    reason: str
    usage: Dict[str, int] = field(default_factory=dict)
    type: str = "stop"


Event = Any  # TextDelta | ReasoningDelta | ToolUse | Stop


class ProviderError(Exception):
    """Raised when a provider call fails (auth, rate-limit, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_headers: Dict[str, str] | None = None,
        response_body: str | None = None,
        is_retryable: bool | None = None,
        is_overflow: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_headers = response_headers
        self.response_body = response_body
        self.is_retryable = is_retryable
        self.is_overflow = is_overflow


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


@runtime_checkable
class Provider(Protocol):
    """A streaming chat completion provider with tool support."""

    id: str

    def stream(
        self,
        *,
        system: str,
        messages: List[Message],
        tools: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> Iterator[Event]:
        """Yield events for one assistant turn.

        `tools` is a list of provider-ready tool specs (see
        ToolRegistry.to_provider_spec). Implementations translate the
        canonical Message list into their wire format.
        """
        ...


def flatten_content(content: List[ContentBlock]) -> List[ContentBlock]:
    """Drop empty text blocks. Mirrors opencode's `normalizeMessages` step
    that scrubs empty content parts before sending."""
    return [b for b in content if not (isinstance(b, TextBlock) and not b.text)]
