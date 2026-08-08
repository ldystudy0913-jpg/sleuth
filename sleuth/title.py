"""Session title generation — port of opencode `SessionPrompt.ensureTitle`.

On the first real user message, if the title is still the default, call the
small model (or main model) with the hidden title prompt and set the title.
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional
from zoneinfo import ZoneInfo

from .messages import Message
from .provider.base import Provider, ProviderError, TextDelta, Stop

if TYPE_CHECKING:
    from .config import Config

PARENT_PREFIX = "New session - "
CHILD_PREFIX = "Child session - "
# Also accept the bare MVP default used before this port
BARE_DEFAULT = "New session"

# Legacy UTC default: New session - 2026-08-08T10:09:12.123Z
_DEFAULT_RE_UTC = re.compile(
    rf"^({re.escape(PARENT_PREFIX)}|{re.escape(CHILD_PREFIX)})"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
# Local default: New session - 2026-08-08 18:09:12
_DEFAULT_RE_LOCAL = re.compile(
    rf"^({re.escape(PARENT_PREFIX)}|{re.escape(CHILD_PREFIX)})"
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
)

_DEFAULT_TZ = "Asia/Shanghai"


def get_timezone_name() -> str:
    """Timezone for session titles / list display (env SLEUTH_TIMEZONE)."""
    raw = (os.environ.get("SLEUTH_TIMEZONE") or _DEFAULT_TZ).strip()
    return raw or _DEFAULT_TZ


def get_zoneinfo():
    """Return ZoneInfo or a fixed UTC+8 fallback if tzdata is unavailable (Windows)."""
    name = get_timezone_name()
    try:
        return ZoneInfo(name)
    except Exception:
        if name in {_DEFAULT_TZ, "Asia/Chongqing", "Asia/Harbin", "PRC"}:
            return datetime.timezone(datetime.timedelta(hours=8), name="UTC+8")
        try:
            return ZoneInfo(_DEFAULT_TZ)
        except Exception:
            return datetime.timezone(datetime.timedelta(hours=8), name="UTC+8")


def format_local_ms(ms: Optional[int], *, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format epoch milliseconds in SLEUTH_TIMEZONE (empty if ms missing)."""
    if not ms:
        return ""
    try:
        dt = datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc)
        return dt.astimezone(get_zoneinfo()).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return ""


def default_title(*, child: bool = False) -> str:
    """Default session title with local (Beijing by default) timestamp."""
    stamp = datetime.datetime.now(get_zoneinfo()).strftime("%Y-%m-%d %H:%M:%S")
    return (CHILD_PREFIX if child else PARENT_PREFIX) + stamp


def is_default_title(title: str) -> bool:
    if not title or title == BARE_DEFAULT:
        return True
    return bool(_DEFAULT_RE_UTC.match(title) or _DEFAULT_RE_LOCAL.match(title))


def _load_title_prompt() -> str:
    p = Path(__file__).resolve().parent / "prompts" / "title.txt"
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Generate a brief title for this conversation. Output ONLY the title."


def ensure_title(
    *,
    title: str,
    messages: List[Message],
    provider: Provider,
    model: str,
    parent_id: Optional[str] = None,
) -> Optional[str]:
    """Return a new title, or None if no generation should happen."""
    if parent_id:
        return None
    if not is_default_title(title):
        return None

    user_msgs = [m for m in messages if m.role == "user" and m.text.strip()]
    if len(user_msgs) != 1:
        return None

    first = user_msgs[0].text.strip()
    if not first:
        return None

    system = _load_title_prompt()
    prompt_msgs = [
        Message.user_text("Generate a title for this conversation:\n" + first),
    ]
    try:
        chunks: List[str] = []
        for event in provider.stream(
            system=system,
            messages=prompt_msgs,
            tools=[],
            model=model,
            max_tokens=64,
            temperature=0.5,
        ):
            if isinstance(event, TextDelta):
                chunks.append(event.text)
            elif isinstance(event, Stop):
                break
    except ProviderError:
        return None

    text = "".join(chunks)
    text = re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
    cleaned = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not cleaned:
        return None
    if len(cleaned) > 100:
        cleaned = cleaned[:97] + "..."
    return cleaned


def resolve_title_model(config: "Config", fallback_model: str) -> str:
    """Prefer `small_model`, else the session model (opencode getSmallModel)."""
    from .config import parse_model_ref

    ref = config.small_model or config.model
    if not ref:
        return fallback_model
    _, model_id = parse_model_ref(ref)
    return model_id or fallback_model
