"""Context compaction — simplified port of opencode `session/compaction.ts`.

When recent token usage approaches the context budget, summarise older turns
with the small/title model and replace the history prefix with a single
summary message, keeping a recent tail intact.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from .messages import Message, TextBlock
from .provider.base import Provider, ProviderError, TextDelta, Stop

if TYPE_CHECKING:
    from .config import Config

COMPACTION_BUFFER = 20_000
DEFAULT_CONTEXT = 128_000
DEFAULT_TAIL_USER_TURNS = 2

_STRUCTURE = """Summarize the conversation so far for continuing the coding task.

Use this exact structure:

## Goal
## Constraints
## Key decisions
## Files / symbols
## Current state
## Next steps
## Open questions

Be terse. Preserve exact paths and identifiers."""


def _load_system() -> str:
    p = Path(__file__).resolve().parent / "prompts" / "compaction.txt"
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Summarize the conversation for continuing work. Do not answer it."


def usable_tokens(config: "Config") -> int:
    """Tokens available for history before compaction (opencode `usable`)."""
    ctx = int(getattr(config, "context_limit", 0) or DEFAULT_CONTEXT)
    if ctx <= 0:
        return 0
    reserved = COMPACTION_BUFFER
    compaction = getattr(config, "compaction", None) or {}
    if isinstance(compaction, dict) and compaction.get("reserved") is not None:
        reserved = int(compaction["reserved"])
    return max(0, ctx - reserved)


def is_overflow(config: "Config", usage: dict) -> bool:
    """Port of opencode `overflow.isOverflow`."""
    compaction = getattr(config, "compaction", None) or {}
    if isinstance(compaction, dict) and compaction.get("auto") is False:
        return False
    budget = usable_tokens(config)
    if budget <= 0:
        return False
    total = usage.get("total") or 0
    if not total:
        total = (
            usage.get("raw_input", usage.get("input", 0))
            + usage.get("raw_output", usage.get("output", 0))
            + usage.get("cache_read", 0)
            + usage.get("cache_write", 0)
        )
    return int(total) >= budget


def _estimate_chars(messages: List[Message]) -> int:
    n = 0
    for m in messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                n += len(b.text)
            else:
                n += len(str(getattr(b, "content", "") or getattr(b, "input", "") or ""))
    return n


def _split_tail(messages: List[Message], keep_user_turns: int) -> Tuple[List[Message], List[Message]]:
    """Keep the last N user-led turns verbatim; summarise the prefix."""
    if keep_user_turns <= 0 or len(messages) < 4:
        return messages, []
    user_idxs = [i for i, m in enumerate(messages) if m.role == "user"]
    if len(user_idxs) <= keep_user_turns:
        return messages, []
    cut = user_idxs[-keep_user_turns]
    return messages[:cut], messages[cut:]


def compact(
    *,
    messages: List[Message],
    provider: Provider,
    model: str,
    previous_summary: Optional[str] = None,
    keep_user_turns: int = DEFAULT_TAIL_USER_TURNS,
) -> Optional[List[Message]]:
    """Return a new message list with a summary prefix, or None on failure."""
    head, tail = _split_tail(messages, keep_user_turns)
    if not head:
        return None

    # Build a compact text view of the head for the summariser
    parts: List[str] = []
    if previous_summary:
        parts.append(f"<previous-summary>\n{previous_summary}\n</previous-summary>")
    for m in head:
        role = m.role
        text = m.text.strip()
        if not text and m.tool_uses:
            text = "; ".join(f"{t.name}({t.input})" for t in m.tool_uses)[:2000]
        if not text:
            # tool results
            from .messages import ToolResultBlock

            chunks = [
                b.content[:1500]
                for b in m.content
                if isinstance(b, ToolResultBlock)
            ]
            text = "\n".join(chunks)[:4000]
        if text:
            parts.append(f"[{role}] {text[:4000]}")

    history_blob = "\n\n".join(parts)
    if _estimate_chars([Message.user_text(history_blob)]) < 500 and not previous_summary:
        return None

    system = _load_system()
    user = Message.user_text(_STRUCTURE + "\n\n<conversation>\n" + history_blob + "\n</conversation>")
    try:
        chunks: List[str] = []
        for event in provider.stream(
            system=system,
            messages=[user],
            tools=[],
            model=model,
            max_tokens=2048,
            temperature=0.2,
        ):
            if isinstance(event, TextDelta):
                chunks.append(event.text)
            elif isinstance(event, Stop):
                break
    except ProviderError:
        return None

    summary = "".join(chunks).strip()
    if not summary:
        return None

    summary_msg = Message.user_text(
        "[compacted context]\n" + summary,
        synthetic=True,
        compacted=True,
    )
    return [summary_msg] + tail
