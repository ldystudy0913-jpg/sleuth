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
            text, new_sess = _expand_command(session, args.prompt, listed_ids=[])
            if new_sess is not None:
                session = new_sess
            if text is None:
                return 1
            session.prompt(text)
        except KeyboardInterrupt:
            session.cancel()
            _print("\n[aborted]\n")
        return 0

    listed_ids: List[str] = []
    _print(
        "sleuth interactive session. Type your prompt; Ctrl+C or 'exit' to quit.\n"
        f"[session {session.id}] title={session.title!r} "
        f"agent={session.agent_name} model={session.model_ref()} "
        f"yolo={'on' if session.yolo else 'off'}\n"
        "Slash: /sessions · /session · /model · /agent · /mcp · /skills · "
        "/usage · /yolo  ·  commands from .opencode/command/*.md\n\n"
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
                text, new_sess = _expand_command(session, line, listed_ids=listed_ids)
                if new_sess is not None:
                    session = new_sess
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


def _expand_command(
    session,
    line: str,
    *,
    listed_ids: Optional[List[str]] = None,
) -> tuple[Optional[str], Optional["Session"]]:
    """Expand slash / custom commands.

    Returns (prompt_text, new_session). Meta commands return (None, None) or
    (None, switched_session). Non-slash lines return (line, None).
    """
    if not line.startswith("/"):
        return line, None
    body = line[1:].strip()
    if not body:
        return line, None
    name, _, rest = body.partition(" ")
    name = name.strip()
    rest = rest.strip()

    if name == "model":
        _handle_model_command(session, rest)
        return None, None
    if name in ("sessions", "session"):
        new_sess = _handle_session_command(session, name, rest, listed_ids=listed_ids)
        return None, new_sess
    if name == "agent":
        _handle_agent_command(session, rest)
        return None, None
    if name == "mcp":
        _handle_mcp_command(session, rest)
        return None, None
    if name == "skills":
        _handle_skills_command(session, rest)
        return None, None
    if name == "usage":
        _handle_usage_command(session, rest)
        return None, None
    if name == "yolo":
        _handle_yolo_command(session, rest)
        return None, None

    cmd = session.config.commands.get(name)
    if cmd is None:
        known = ", ".join(sorted(session.config.commands)) or "(none)"
        _print(
            f"unknown command /{name}. known: /sessions, /session, /model, "
            f"/agent, /mcp, /skills, /usage, /yolo, {known}\n"
        )
        return None, None
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
        try:
            session.set_agent(cmd.agent, yolo=session.yolo)
        except Exception as exc:
            _print(f"agent switch for /{name} failed: {exc}\n")
            return None, None
    return text, None


def _handle_session_command(
    session,
    name: str,
    rest: str,
    *,
    listed_ids: Optional[List[str]] = None,
) -> Optional["Session"]:
    """``/sessions [n]`` list, ``/session`` show current, ``/session n|id`` switch."""
    from .session_browse import build_session_list_rows, resolve_session_id
    from .title import format_local_ms

    if session.is_busy():
        _print("busy: wait for the current turn to finish before switching sessions\n")
        return None

    store = session.store
    if store is None:
        _print("no session store configured; cannot list/switch sessions\n")
        return None

    user_id = session.user_id or getattr(session.config, "user_id", "local") or "local"

    if name == "sessions" or (name == "session" and rest.startswith("list")):
        limit = 20
        arg = rest
        if name == "session" and rest.startswith("list"):
            arg = rest[4:].strip()
        if arg.isdigit():
            limit = int(arg)
        rows = build_session_list_rows(store, user_id=user_id, limit=limit)
        if listed_ids is not None:
            listed_ids.clear()
            listed_ids.extend(str(r["id"]) for r in rows)
        if not rows:
            _print(f"no sessions for user={user_id!r}\n")
            return None
        _print(f"sessions for user={user_id!r} (newest first):\n")
        for r in rows:
            short = str(r["id"])[:12]
            when = r.get("time_updated_local") or "-"
            title = (r.get("title") or "").replace("\n", " ")
            preview = r.get("preview") or "(no user message yet)"
            _print(
                f"  {r['index']:>2}. [{short}…] {when}\n"
                f"      title: {title}\n"
                f"      preview: {preview}\n"
            )
        _print("Switch with: /session <n|id>\n")
        return None

    if name == "session" and not rest:
        when = ""
        if store.get_session(session.id):
            rec = store.get_session(session.id)
            when = format_local_ms(rec.time_updated) if rec else ""
        _print(
            f"current session id={session.id}\n"
            f"  title={session.title!r}\n"
            f"  agent={session.agent_name} model={session.model_ref()}\n"
            f"  updated={when or '-'}\n"
            f"  tip: /sessions to list, /session <n|id> to switch\n"
        )
        return None

    # Build rows from last list or fresh list for index resolution
    rows = []
    if listed_ids:
        for i, sid in enumerate(listed_ids, start=1):
            rows.append({"index": i, "id": sid})
    else:
        rows = build_session_list_rows(store, user_id=user_id, limit=20)
        if listed_ids is not None:
            listed_ids.clear()
            listed_ids.extend(str(r["id"]) for r in rows)

    sid = resolve_session_id(rows, rest, store=store, user_id=user_id)
    if not sid:
        _print(
            f"session not found: {rest!r}. "
            "Use /sessions then /session <n>, or a full/prefix id.\n"
        )
        return None
    if sid == session.id:
        _print(f"already on session {sid}\n")
        return None

    from .session import Session

    new_sess = Session.load(
        provider=session.provider,
        registry=session.registry,
        config=session.config,
        workdir=session.workdir,
        permission=session.permission,
        store=store,
        session_id_value=sid,
        agent_name=session.agent_name,
        model_id=session.model_id,
        renderer=session.renderer,
    )
    new_sess.yolo = session.yolo
    new_sess._mcp_tool_names = getattr(session, "_mcp_tool_names", set())
    new_sess._mcp_manager = getattr(session, "_mcp_manager", None)
    _print(
        f"switched to session {new_sess.id}\n"
        f"  title={new_sess.title!r} agent={new_sess.agent_name} "
        f"model={new_sess.model_ref()} messages={len(new_sess.messages)}\n"
    )
    return new_sess


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


def _handle_agent_command(session, rest: str) -> None:
    """``/agent`` list or ``/agent <name>`` sticky switch."""
    from .catalog import agents_payload

    if session.is_busy():
        _print("busy: wait for the current turn to finish before /agent\n")
        return
    mcp_manager = getattr(session, "_mcp_manager", None)
    if mcp_manager is None:
        try:
            from .mcp import get_manager

            mcp_manager = get_manager(session.config)
            session._mcp_manager = mcp_manager
        except Exception:
            mcp_manager = None
    payload = agents_payload(
        session.config, include_hidden=False, mcp_manager=mcp_manager
    )
    if not rest:
        _print(f"current agent: {session.agent_name}\n")
        _print(f"default: {payload.get('default') or '-'}\n")
        agents = payload.get("agents") or []
        if not agents:
            _print("no agents configured\n")
            return
        _print("agents:\n")
        for a in agents:
            mark = " *" if a.get("name") == session.agent_name else ""
            avail = "ok" if a.get("available") else "down"
            src = a.get("source") or "local"
            mcp = a.get("mcp_server") or "-"
            desc = (a.get("description") or "").replace("\n", " ")
            if len(desc) > 72:
                desc = desc[:69] + "..."
            _print(
                f"  {a.get('name')}{mark}  [{avail}/{src}"
                f"{'' if src == 'local' else ' @ ' + str(mcp)}]\n"
                f"      {desc or '(no description)'}\n"
            )
        _print("Switch with: /agent <name>\n")
        return
    name = rest.strip()
    entry = next(
        (a for a in (payload.get("agents") or []) if a.get("name") == name),
        None,
    )
    if entry is not None and not entry.get("available", True):
        _print(
            f"warning: agent {name!r} MCP server "
            f"{entry.get('mcp_server')!r} is not connected; switching anyway\n"
        )
    try:
        resolved = session.set_agent(name, yolo=session.yolo)
    except Exception as exc:
        _print(f"agent switch failed: {exc}\n")
        return
    _print(f"agent set to {resolved}\n")


def _handle_mcp_command(session, rest: str) -> None:
    """``/mcp`` status or ``/mcp reload``."""
    from .catalog import mcp_status_dict

    if rest and rest.split()[0].lower() not in ("reload", "status", "list"):
        _print("usage: /mcp  |  /mcp reload\n")
        return
    if rest.lower().startswith("reload"):
        if session.is_busy():
            _print("busy: wait for the current turn to finish before /mcp reload\n")
            return
        from .app import resync_session_mcp

        try:
            result = resync_session_mcp(session)
        except Exception as exc:
            _print(f"mcp reload failed: {exc}\n")
            return
        servers = result.get("servers") or []
        ok_n = sum(1 for s in servers if s.get("connected"))
        _print(
            f"mcp reloaded: {ok_n}/{len(servers)} servers connected, "
            f"{len(result.get('tools') or [])} tools, "
            f"{len(result.get('agents') or [])} agents\n"
        )
        for err in result.get("errors") or []:
            _print(f"  error: {err}\n")
        return

    status = mcp_status_dict(session.config)
    servers = status.get("servers") or []
    if not servers:
        _print("no MCP servers configured\n")
    else:
        _print("MCP servers:\n")
        for s in servers:
            state = "connected" if s.get("connected") else "down"
            err = s.get("error") or ""
            agents = ", ".join(s.get("agents") or []) or "-"
            _print(
                f"  {s.get('name')}: {state}"
                f"{(' — ' + err) if err else ''}\n"
                f"      url={s.get('url') or '-'}\n"
                f"      agents={agents}\n"
            )
    tools = status.get("tools") or []
    agents = status.get("agents") or []
    _print(f"tools ({len(tools)}): {', '.join(tools) or '-'}\n")
    _print(f"card agents ({len(agents)}): {', '.join(agents) or '-'}\n")
    for err in status.get("errors") or []:
        _print(f"error: {err}\n")
    _print("Reload with: /mcp reload\n")


def _handle_skills_command(session, rest: str) -> None:
    """``/skills`` list or ``/skills reload``."""
    from .catalog import skills_payload

    if rest and rest.split()[0].lower() not in ("reload", "list", "status"):
        _print("usage: /skills  |  /skills reload\n")
        return
    if rest.lower().startswith("reload"):
        if session.is_busy():
            _print("busy: wait for the current turn to finish before /skills reload\n")
            return
        from .app import reload_skills

        try:
            skills = reload_skills(session.config, session.workdir)
        except Exception as exc:
            _print(f"skills reload failed: {exc}\n")
            return
        _print(f"skills reloaded: {len(skills)} ({', '.join(sorted(skills.keys())) or '-'})\n")
        return

    rows = skills_payload(session.config, session.workdir)
    if not rows:
        _print("no skills loaded\n")
        return
    _print(f"skills ({len(rows)}):\n")
    for s in sorted(rows, key=lambda r: r.get("name") or ""):
        desc = (s.get("description") or "").replace("\n", " ")
        if len(desc) > 72:
            desc = desc[:69] + "..."
        _print(
            f"  {s.get('name')}\n"
            f"      {desc or '(no description)'}\n"
            f"      {s.get('location') or '-'}\n"
        )
    _print("Reload with: /skills reload\n")


def _handle_usage_command(session, rest: str) -> None:
    """``/usage`` — aggregated usage for the current user."""
    del rest  # unused; keep signature consistent
    store = session.store
    if store is None or not hasattr(store, "sum_usage"):
        _print("no session store configured; cannot show usage\n")
        return
    user_id = session.user_id or getattr(session.config, "user_id", "local") or "local"
    try:
        u = store.sum_usage(user_id)
    except Exception as exc:
        _print(f"usage query failed: {exc}\n")
        return
    _print(
        f"usage for user={user_id!r}\n"
        f"  events={u.get('events', 0)}\n"
        f"  tokens_input={u.get('tokens_input', 0)} "
        f"tokens_output={u.get('tokens_output', 0)} "
        f"tokens_reasoning={u.get('tokens_reasoning', 0)}\n"
        f"  cost={float(u.get('cost') or 0):.6f}\n"
        f"  session_cost_this_process={float(getattr(session, '_session_cost', 0) or 0):.6f}\n"
    )


def _handle_yolo_command(session, rest: str) -> None:
    """``/yolo`` show or ``/yolo on|off``."""
    if session.is_busy():
        _print("busy: wait for the current turn to finish before /yolo\n")
        return
    arg = (rest or "").strip().lower()
    if not arg:
        _print(f"yolo: {'on' if session.yolo else 'off'}\n")
        _print("Toggle with: /yolo on | /yolo off\n")
        return
    if arg in ("on", "1", "true", "yes"):
        session.set_yolo(True)
        _print("yolo on (auto-approve tools)\n")
        return
    if arg in ("off", "0", "false", "no"):
        session.set_yolo(False)
        _print("yolo off (ask before tools)\n")
        return
    _print("usage: /yolo  |  /yolo on  |  /yolo off\n")
