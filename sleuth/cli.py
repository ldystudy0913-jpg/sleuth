"""Rich-based renderer: pretty streaming output for the agent loop.

Rich is a soft dependency — if it isn't installed we fall back to plain
prints so the tool still runs out of the box.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

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


class RichRenderer:
    """Render streaming events to the terminal."""

    def __init__(self, *, show_tools: bool = True):
        self.show_tools = show_tools
        self._text_open = False  # are we mid-stream of assistant text?

    def on_step(self, step: int, max_steps: int) -> None:
        pass  # keep output clean; uncomment for debugging
        # if _HAS_RICH: _console.log(f"[dim]step {step}/{max_steps}[/dim]")

    def on_text(self, text: str) -> None:
        # Stream raw text as it arrives; markdown rendering happens at the
        # end via on_stop when we have the full block. Streaming raw keeps
        # latency low and avoids partial-markdown render glitches.
        _print(text)
        self._text_open = True

    def on_reasoning(self, text: str) -> None:
        """Render the model's thinking in dim/gray (opencode textMuted)."""
        self._newline_if_open()
        if _HAS_RICH:
            _console.print(text, style="dim", end="")
        else:
            # ANSI dim prefix \x1b[2m ... \x1b[0m
            _print("\x1b[2m" + text + "\x1b[0m")

    def on_tool_start(self, name: str, args: dict) -> None:
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
        self._newline_if_open()
        if _HAS_RICH:
            _console.print(
                f"[yellow]retry {attempt}:[/yellow] {message} "
                f"[dim](waiting {wait:.1f}s)[/dim]"
            )
        else:
            print(f"retry {attempt}: {message} (waiting {wait:.1f}s)")

    def on_error(self, message: str) -> None:
        self._newline_if_open()
        if _HAS_RICH:
            _console.print(f"[red]error:[/red] {message}")
        else:
            print(f"error: {message}")

    def _newline_if_open(self) -> None:
        if self._text_open:
            _print("\n")
            self._text_open = False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_session(args) -> "Session":
    from pathlib import Path

    from .agent import ruleset_for
    from .config import load
    from .permission import Permission, allow_all_rules, from_config as permission_from_config
    from .provider.factory import resolve_model
    from .session import Session
    from .storage.sqlite import SQLiteStore
    from .tools.registry import ToolRegistry

    config = load()

    agent_name = args.agent or config.default_agent
    provider, model_id = resolve_model(config, agent_name)

    # permission ruleset: --yolo wins, else agent baseline + agent/config overrides
    if args.yolo:
        rules = allow_all_rules()
    else:
        rules = ruleset_for(agent_name)
        agent_cfg = config.agent(agent_name)
        if agent_cfg.permission:
            rules = rules + permission_from_config(agent_cfg.permission)
        if config.permission:
            # opencode fromConfig: {bash:"ask"} or {bash:{"git *":"allow"}}
            rules = rules + permission_from_config(config.permission)

    permission = Permission(rules=rules)
    store = SQLiteStore()

    # resume a persisted session when requested
    if getattr(args, "session_id", None):
        return Session.load(
            provider=provider, registry=ToolRegistry(), config=config,
            workdir=Path.cwd(), permission=permission, store=store,
            session_id_value=args.session_id, agent_name=agent_name,
            model_id=model_id,
            renderer=RichRenderer(show_tools=not args.print_mode),
        )
    if getattr(args, "continue_session", False):
        recent = store.list_sessions(directory=str(Path.cwd()), limit=1)
        if recent:
            return Session.load(
                provider=provider, registry=ToolRegistry(), config=config,
                workdir=Path.cwd(), permission=permission, store=store,
                session_id_value=recent[0].id, agent_name=agent_name,
                model_id=model_id,
                renderer=RichRenderer(show_tools=not args.print_mode),
            )

    session = Session(
        provider=provider,
        registry=ToolRegistry(),
        config=config,
        workdir=Path.cwd(),
        permission=permission,
        agent_name=agent_name,
        model_id=model_id,
        renderer=RichRenderer(show_tools=not args.print_mode),
        store=store,
    )
    return session


def _parse_args(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="sleuth",
        description="sleuth — a Python coding agent (opencode port)",
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

    # Load .env from cwd and ~/.config/opencode/.env (does not overwrite real
    # env vars). Keeps API keys out of the committed opencode.json.
    from pathlib import Path
    from .util.env import load_dotenv
    load_dotenv(Path.cwd())

    # --model override: inject into config at runtime
    from .config import Config, parse_model_ref

    session = _build_session(args)
    if args.model:
        # rebuild config with the override model for the active agent
        session.config.model = args.model
        provider_id, model_id = parse_model_ref(args.model)
        from .provider.factory import build_provider
        session.provider = build_provider(session.config, provider_id)
        session.model_id = model_id

    if getattr(args, "revert_message_id", None):
        ok = session.revert_to(args.revert_message_id)
        _print("reverted\n" if ok else "revert failed (no snapshot)\n")
        return 0 if ok else 1

    if args.prompt:
        # non-interactive single-shot. Ctrl+C aborts the running turn.
        _print(f"[session {session.id}]\n")
        try:
            text = _expand_command(session, args.prompt)
            if text is None:
                return 1
            session.prompt(text)
        except KeyboardInterrupt:
            session.cancel()
            _print("\n[aborted]\n")
        return 0

    # interactive REPL
    _print(
        "sleuth interactive session. Type your prompt; Ctrl+C or 'exit' to quit.\n"
        f"[session {session.id}] title={session.title!r}\n"
        "Slash commands from .opencode/command/*.md: /name [args]\n\n"
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
                # Ctrl+C mid-turn: abort the running turn, stay in the REPL.
                session.cancel()
                _print("\n[aborted; press enter to continue]\n")
            _print("\n\n")
    except KeyboardInterrupt:
        _print("\nbye.\n")
    return 0


def _expand_command(session, line: str) -> Optional[str]:
    """Expand `/command args` using config.commands (opencode command templates).

    Returns the prompt text, or None if the command was unknown (error printed).
    """
    if not line.startswith("/"):
        return line
    body = line[1:].strip()
    if not body:
        return line
    name, _, rest = body.partition(" ")
    name = name.strip()
    rest = rest.strip()
    cmd = session.config.commands.get(name)
    if cmd is None:
        known = ", ".join(sorted(session.config.commands)) or "(none)"
        _print(f"unknown command /{name}. known: {known}\n")
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
