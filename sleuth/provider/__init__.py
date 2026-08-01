"""LLM provider abstraction.

opencode wraps the Vercel AI SDK; each provider exposes a `LanguageModelV3`
and the AI SDK drives `streamText`, normalising everything to a single event
stream (text-delta, tool-call, tool-result, finish, ...). We port that
*event protocol* directly and implement one concrete provider against it:
the OpenAI SDK, which also speaks to any OpenAI-compatible gateway.

Keeping a small, explicit event union here means the session loop never
branches on provider identity — it just consumes events.
"""
from __future__ import annotations

from .base import (
    Event,
    TextDelta,
    ToolUse,
    Stop,
    Provider,
    ProviderError,
)

__all__ = [
    "Event",
    "TextDelta",
    "ToolUse",
    "Stop",
    "Provider",
    "ProviderError",
]
