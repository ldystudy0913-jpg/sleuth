"""Rich-based renderer: pretty streaming output for the agent loop.

Rich is a soft dependency — if it isn't installed we fall back to plain
prints so the tool still runs out of the box.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, List, Optional

from .tools.base import ToolResult

if TYPE_CHECKING:
    from .session import Session

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    _console = Console()
    _HAS_RICH = True
except Exception:  # pragma: no cover
    _console = None
    _HAS_RICH = False

# Thinking block collapses when longer than this many lines (env override).
_DEFAULT_REASONING_COLLAPSE_LINES = 8


def _reasoning_collapse_lines() -> int:
    raw = os.environ.get("SLEUTH_REASONING_COLLAPSE_LINES")
    if raw is None or raw == "":
        return _DEFAULT_REASONING_COLLAPSE_LINES
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_REASONING_COLLAPSE_LINES


def _print(text: str) -> None:
    if _HAS_RICH:
        _console.print(text, end="")
    else:
        print(text, end="")


def _markdown(text: str) -> None:
    if _HAS_RICH:
        _console.print(Markdown(text))
    else:
        print(text)


def _panel(text: str, title: str, style: str = "cyan") -> None:
    if _HAS_RICH:
        _console.print(Panel(text, title=title, border_style=style, expand=False))
    else:
        print(f"--- {title} ---\n{text}\n---")


def _count_lines(text: str) -> int:
    if not text:
        return 0
    # splitlines() drops a trailing bare newline's empty last line — fine for preview.
    return max(1, len(text.splitlines()))


class RichRenderer:
    """Render streaming events to the terminal."""

    def __init__(
        self,
        *,
        show_tools: bool = True,
        interactive: bool = True,
        reasoning_collapse_lines: Optional[int] = None,
    ):
        self.show_tools = show_tools
        self.interactive = interactive
        self.reasoning_collapse_lines = (
            _reasoning_collapse_lines()
            if reasoning_collapse_lines is None
            else max(0, int(reasoning_collapse_lines))
        )
        self._text_open = False
        self._reasoning_open = False
        self._reasoning_parts: List[str] = []
        self._reasoning_status_drawn = False
        self._pending_full_reasoning: Optional[str] = None

    def on_step(self, step: int, max_steps: int) -> None:
        pass

    def on_text(self, text: str) -> None:
        self._finalize_reasoning(allow_expand=True)
        if not self._text_open:
            _print("\n")
        _print(text)
        self._text_open = True

    def on_reasoning(self, text: str) -> None:
        """Buffer thinking; show a live line counter, collapse when finished."""
        import sys

        if self._text_open:
            self._newline_if_open()
        if not self._reasoning_open:
            self._reasoning_open = True
            self._reasoning_parts = []
            self._reasoning_status_drawn = False
            self._pending_full_reasoning = None
        self._reasoning_parts.append(text)
        body = "".join(self._reasoning_parts)
        n = _count_lines(body)
        status = f"thinking... {n} line{'s' if n != 1 else ''}"
        # Carriage-return status (avoid dumping the full stream mid-flight).
        sys.stdout.write("\r\x1b[2m" + status + "\x1b[0m\x1b[K")
        sys.stdout.flush()
        self._reasoning_status_drawn = True

    def on_tool_start(self, name: str, args: dict) -> None:
        # Mid-tool-loop: collapse without blocking for expand.
        self._finalize_reasoning(allow_expand=False)
        if not self.show_tools:
            return
        self._newline_if_open()
        arg_str = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:4])
        _print(f"\n⚙ {name}({arg_str})\n")

    def on_tool_result(self, name: str, result: ToolResult) -> None:
        if not self.show_tools:
            return
        self._newline_if_open()
        body = result.output
        if len(body) > 600:
            body = body[:600] + "...[truncated]"
        mark = "✗" if result.is_error else "✓"
        _print(f"  {mark} {result.title}\n")

    def on_stop(self, reason: str, usage: dict) -> None:
        self._finalize_reasoning(allow_expand=True)
        self._offer_pending_expand()
        self._newline_if_open()
        if usage and _HAS_RICH:
            extras = ""
            if usage.get("reasoning"):
                extras += f" reason={usage.get('reasoning', 0)}"
            if usage.get("cache_read"):
                extras += f" cache_r={usage.get('cache_read', 0)}"
            _console.print(
                f"[dim]({reason}; in={usage.get('input', 0)} "
                f"out={usage.get('output', 0)}{extras} tokens)[/dim]"
            )
        elif usage:
            print(
                f"({reason}; in={usage.get('input', 0)} "
                f"out={usage.get('output', 0)} tokens)"
            )

    def on_retry(self, attempt: int, message: str, wait: float) -> None:
        self._finalize_reasoning(allow_expand=False)
        self._newline_if_open()
        if _HAS_RICH:
            _console.print(
                f"[yellow]retry {attempt}:[/yellow] {message} "
                f"[dim](waiting {wait:.1f}s)[/dim]"
            )
        else:
            print(f"retry {attempt}: {message} (waiting {wait:.1f}s)")

    def on_error(self, message: str) -> None:
        self._finalize_reasoning(allow_expand=False)
        self._newline_if_open()
        if _HAS_RICH:
            _console.print(f"[red]error:[/red] {message}")
        else:
            print(f"error: {message}")

    def _newline_if_open(self) -> None:
        if self._text_open:
            _print("\n")
            self._text_open = False

    def _finalize_reasoning(self, *, allow_expand: bool = False) -> None:
        import sys

        if not self._reasoning_open:
            return
        body = "".join(self._reasoning_parts).strip("\n")
        self._reasoning_open = False
        self._reasoning_parts = []
        if self._reasoning_status_drawn:
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
            self._reasoning_status_drawn = False

        if not body:
            return

        lines = body.splitlines() or [body]
        limit = self.reasoning_collapse_lines
        # 0 = always collapse (still expandable when interactive).
        collapsed = limit == 0 or len(lines) > limit

        if not collapsed:
            self._emit_reasoning_block(body, collapsed=False)
            _print("\n")
            return

        preview_n = 3 if limit == 0 else min(3, max(1, limit))
        preview = "\n".join(lines[:preview_n])
        hidden = max(0, len(lines) - preview_n)
        summary = f"{preview}\n... (+{hidden} lines)" if hidden else preview
        self._emit_reasoning_block(summary, collapsed=True, total_lines=len(lines))
        _print("\n")

        if allow_expand and self.interactive and not os.environ.get("SLEUTH_REASONING_NO_PROMPT"):
            self._pending_full_reasoning = None
            self._prompt_expand(body)
        elif self.interactive and not os.environ.get("SLEUTH_REASONING_NO_PROMPT"):
            # Defer expand until answer/stop so tool loops are not blocked.
            self._pending_full_reasoning = body

    def _offer_pending_expand(self) -> None:
        body = self._pending_full_reasoning
        if not body:
            return
        self._pending_full_reasoning = None
        if self.interactive and not os.environ.get("SLEUTH_REASONING_NO_PROMPT"):
            self._prompt_expand(body)

    def _prompt_expand(self, body: str) -> None:
        try:
            ans = input("  expand thinking? [e=expand / Enter=skip] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
            _print("\n")
        if ans in ("e", "expand", "y", "yes"):
            self._emit_reasoning_block(body, collapsed=False)
            _print("\n")

    def _emit_reasoning_block(
        self,
        body: str,
        *,
        collapsed: bool,
        total_lines: Optional[int] = None,
    ) -> None:
        title = "thinking"
        if collapsed:
            n = total_lines if total_lines is not None else _count_lines(body)
            title = f"thinking (collapsed, {n} lines)"
        if _HAS_RICH:
            _console.print()
            _console.print(
                Panel(
                    body,
                    title=title,
                    border_style="dim",
                    expand=False,
                    style="dim",
                )
            )
        else:
            print(f"\n--- {title} ---\n{body}\n---")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_session(args) -> "Session":
    from pathlib import Path

    from .app import build_session
    from .config import load

    config = load(Path.cwd())
    if getattr(args, "user", None):
        config.user_id = args.user
    renderer = RichRenderer(
        show_tools=not args.print_mode,
        interactive=not args.print_mode,
    )
    return build_session(
        config=config,
        workdir=Path.cwd(),
        agent_name=args.agent,
        user_id=config.user_id,
        session_id=getattr(args, "session_id", None),
        continue_latest=bool(getattr(args, "continue_session", False)),
        yolo=bool(args.yolo),
        renderer=renderer,
    )


def _parse_args(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="sleuth",
        description="sleuth — a Python coding agent",
    )
    parser.add_argument("prompt", nargs="?", help="prompt; if omitted, starts interactive REPL")
    parser.add_argument("--agent", default=None, help="agent to use (build, plan, or custom)")
    parser.add_argument("--model", default=None, help='model ref, e.g. "openai/gpt-4o"')
    parser.add_argument("--yolo", action="store_true", help="auto-approve all tool actions")
    parser.add_argument("--print", dest="print_mode", action="store_true",
                        help="print-only mode (no tool display); for scripting")
    parser.add_argument("--session", dest="session_id", default=None,
                        help="resume a persisted session by id")
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true",
                        help="continue the most recent session in this directory")
    parser.add_argument("--revert", dest="revert_message_id", default=None,
                        help="revert working tree to snapshot at the given assistant message id")
    parser.add_argument("--user", default=None,
                        help="user id for session/usage isolation (or SLEUTH_USER_ID)")
    parser.add_argument("--refresh-skills", action="store_true",
                        help="force reload skills from paths/urls/s3 before starting")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    # Force UTF-8 stdio on Windows so streamed non-ASCII text (CJK, emojis)
    # doesn't come out as GBK mojibake on a cp936 console.
    import sys
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # Load .env from cwd (does not overwrite already-exported env vars).
    from pathlib import Path
    from .util.env import load_dotenv
    load_dotenv(Path.cwd())

    if getattr(args, "refresh_skills", False):
        from .app import reload_skills
        from .config import load
        reload_skills(load(Path.cwd()), Path.cwd())

    session = _build_session(args)
    try:
        return _run_session(args, session)
    finally:
        try:
            from .mcp import shutdown_manager
            shutdown_manager()
        except Exception:
            pass


def _run_session(args, session) -> int:
    if args.model:
        try:
            ref = session.set_model(args.model)
            _print(f"model: {ref}\n")
        except Exception as exc:
            _print(f"error: invalid --model: {exc}\n")
            return 1

    if getattr(args, "revert_message_id", None):
        ok = session.revert_to(args.revert_message_id)
        _print("reverted\n" if ok else "revert failed (no snapshot)\n")
        return 0 if ok else 1

    if args.prompt:
        _print(f"[session {session.id}] model={session.model_ref()}\n")
        try:
            text = _expand_command(session, args.prompt)
            if text is None:
                return 1
            session.prompt(text)
        except KeyboardInterrupt:
            session.cancel()
            _print("\n[aborted]\n")
        return 0

    _print(
        "sleuth interactive session. Type your prompt; Ctrl+C or 'exit' to quit.\n"
        f"[session {session.id}] title={session.title!r} model={session.model_ref()}\n"
        "Slash: /model [alias|provider/model]  ·  commands from .opencode/command/*.md\n\n"
    )
    try:
        while True:
            try:
                line = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                _print("\nbye.\n")
                break
            if not line:
                continue
            if line in ("exit", "quit", ":q"):
                break
            try:
                text = _expand_command(session, line)
                if text is None:
                    continue
                session.prompt(text)
                if session.title:
                    _print(f"\n[title: {session.title}]\n")
            except KeyboardInterrupt:
                session.cancel()
                _print("\n[aborted; press enter to continue]\n")
            _print("\n\n")
    except KeyboardInterrupt:
        _print("\nbye.\n")
    return 0


def _expand_command(session, line: str) -> Optional[str]:
    """Expand `/command args` using config.commands (opencode command templates).

    Built-in meta-commands (``/model``) are handled first and return None so
    they do not start an LLM turn.

    Returns the prompt text, or None if the command was a meta-command or
    unknown (error printed).
    """
    if not line.startswith("/"):
        return line
    body = line[1:].strip()
    if not body:
        return line
    name, _, rest = body.partition(" ")
    name = name.strip()
    rest = rest.strip()

    if name == "model":
        return _handle_model_command(session, rest)

    cmd = session.config.commands.get(name)
    if cmd is None:
        known = ", ".join(sorted(session.config.commands)) or "(none)"
        _print(f"unknown command /{name}. known: /model, {known}\n")
        return None
    template = cmd.template or ""
    # Simple $ARGUMENTS / {{args}} substitution (opencode-style)
    if "$ARGUMENTS" in template:
        text = template.replace("$ARGUMENTS", rest)
    elif "{{args}}" in template:
        text = template.replace("{{args}}", rest)
    elif rest:
        text = template + "\n\n" + rest
    else:
        text = template
    if cmd.agent:
        session.agent_name = cmd.agent
    return text


def _handle_model_command(session, rest: str) -> None:
    """``/model`` list or ``/model alias|provider/model`` switch."""
    if session.is_busy():
        _print("busy: wait for the current turn to finish before /model\n")
        return None
    if not rest:
        current = session.model_ref()
        _print(f"current model: {current}\n")
        aliases = session.config.models
        if aliases:
            _print("configured models (SLEUTH_MODELS):\n")
            for alias in sorted(aliases):
                label = session.config.model_entry_label(alias)
                resolved = session.config.prepare_model_ref(alias)
                mark = " *" if resolved == current or alias == current else ""
                _print(f"  {alias}: {label}{mark}\n")
        else:
            _print(
                "no model catalog. Set SLEUTH_MODELS with per-model "
                "apiKey/baseURL, or pass provider/model\n"
            )
        return None
    try:
        ref = session.set_model(rest)
    except Exception as exc:
        _print(f"model switch failed: {exc}\n")
        return None
    _print(f"model set to {ref}\n")
    return None
