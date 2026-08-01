"""Output truncation.

Tool output can be huge (e.g. a 100k-line log). opencode truncates every
tool result through a `Truncate` service with configurable line/byte limits.
This is the Python equivalent: a single pure function applied at the tool
wrapper boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Limits:
    max_lines: int = 2000
    max_bytes: int = 50_000


DEFAULT = Limits()


def truncate(text: str, limits: Limits = DEFAULT) -> tuple[str, bool]:
    """Return (possibly truncated text, was_truncated).

    Both a line cap and a byte cap are enforced; whichever triggers first
    wins. A trailing notice is appended when truncation happens so the model
    knows there is more data it can re-read via the read tool.
    """
    if not text:
        return "", False

    lines = text.splitlines(keepends=True)
    truncated = False

    if len(lines) > limits.max_lines:
        lines = lines[: limits.max_lines]
        truncated = True

    out = "".join(lines)

    if len(out.encode("utf-8", "ignore")) > limits.max_bytes:
        # Slice on characters first, then back off to stay under the byte cap.
        cut = out
        while len(cut.encode("utf-8", "ignore")) > limits.max_bytes and cut:
            cut = cut[: len(cut) - 1]
        out = cut
        truncated = True

    if truncated:
        out += "\n...[output truncated]\n"
    return out, truncated
