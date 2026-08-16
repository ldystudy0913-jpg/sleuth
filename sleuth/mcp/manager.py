"""MCP client — remote URL (Streamable HTTP) tools for sleuth.

Connect configured servers, list_tools, and call_tool. OAuth and stdio
local servers are out of MVP scope.

Connections are parallel with per-server timeouts so one dead server cannot
block the others. Down servers retry in the background (``mcp_retry_seconds``).
Use ``reload()`` to reconnect immediately.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config, McpServerConfig

# Wait this long at start() for servers that are already up; the rest retry in background.
_START_WAIT_S = 2.0


def sanitize_name(name: str) -> str:
    """Make a safe tool-name fragment for MCP catalog entries."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower() or "mcp"


def _is_cancel_scope_error(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and "cancel scope" in str(exc)


def _iter_excs(exc: BaseException):
    """Flatten ExceptionGroup-like objects without requiring 3.11+ builtins."""
    sub = getattr(exc, "exceptions", None)
    if (
        isinstance(sub, (list, tuple))
        and sub
        and all(isinstance(e, BaseException) for e in sub)
    ):
        for e in sub:
            yield from _iter_excs(e)
        return
    yield exc


def _exc_message(exc: BaseException) -> str:
    """Human-readable connect error; skip anyio cancel-scope cleanup noise."""
    found = [e for e in _iter_excs(exc) if not _is_cancel_scope_error(e)]
    if not found:
        found = list(_iter_excs(exc))
    chosen = found[0] if found else exc
    msg = str(chosen).strip()
    if isinstance(chosen, asyncio.TimeoutError) or type(chosen).__name__ in (
        "TimeoutError",
        "CancelledError",
    ):
        return msg or "timed out"
    if not msg:
        return type(chosen).__name__
    if len(msg) > 400:
        return msg[:400] + "..."
    return msg


def _mcp_loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Drop detached anyio athrow tasks after a failed MCP HTTP connect."""
    exc = context.get("exception")
    if exc is not None and any(_is_cancel_scope_error(e) for e in _iter_excs(exc)):
        return
    msg = str(context.get("message") or "")
    if "cancel scope" in msg:
        return
    loop.default_exception_handler(context)


@dataclass
class McpToolInfo:
    server: str
    name: str  # original tool name on the server
    qualified: str  # server_toolname for the model
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class McpServerStatus:
    name: str
    url: str = ""
    connected: bool = False
    error: Optional[str] = None
    agent: bool = False


class McpManager:
    """Owns remote MCP sessions on a background asyncio event loop."""

    def __init__(self, config: Config):
        self.config = config
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._sessions: Dict[str, Any] = {}
        # Per-server lifetime task owns the MCP async context (anyio cancel scope).
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stop: Dict[str, asyncio.Event] = {}
        self._tools: Dict[str, McpToolInfo] = {}
        self._errors: List[str] = []
        self._status: Dict[str, McpServerStatus] = {}
        self._started = False
        self._agent_cards: Dict[str, dict] = {}  # agent name -> card JSON
        self._agent_card_servers: Dict[str, str] = {}  # agent name -> mcp server name
        self._atexit_registered = False
        self._connecting: set[str] = set()
        self._retry_task: Optional[asyncio.Task] = None
        self._closing = False

    @property
    def errors(self) -> List[str]:
        return list(self._errors)

    @property
    def tools(self) -> Dict[str, McpToolInfo]:
        return dict(self._tools)

    @property
    def agent_cards(self) -> Dict[str, dict]:
        return dict(self._agent_cards)

    @property
    def agent_card_servers(self) -> Dict[str, str]:
        return dict(self._agent_card_servers)

    def server_statuses(self) -> List[McpServerStatus]:
        """Snapshot of configured remote servers and connection state."""
        out: List[McpServerStatus] = []
        for s in self.config.enabled_mcp_servers():
            if s.type != "remote" or not s.url:
                continue
            st = self._status.get(s.name)
            if st is None:
                out.append(
                    McpServerStatus(
                        name=s.name,
                        url=s.url or "",
                        connected=s.name in self._sessions,
                        agent=bool(getattr(s, "agent", False)),
                    )
                )
            else:
                out.append(st)
        return out

    def _per_server_timeout_s(self) -> float:
        raw = self.config.mcp_timeout.get("per_server")
        if raw is None:
            raw = self.config.mcp_timeout.get("startup", 30_000)
        try:
            ms = int(raw)
        except (TypeError, ValueError):
            ms = 30_000
        # Clamp: at least 3s, at most 60s per server for isolation.
        return max(3.0, min(60.0, ms / 1000.0))

    def _retry_interval_s(self) -> float:
        try:
            raw = int(getattr(self.config, "mcp_retry_seconds", 15) or 0)
        except (TypeError, ValueError):
            raw = 15
        return max(0.0, float(raw))

    def _set_server_error(self, name: str, msg: Optional[str]) -> None:
        prefix = f"mcp[{name}]:"
        self._errors = [e for e in self._errors if not str(e).startswith(prefix)]
        if msg:
            self._errors.append(f"{prefix} {msg}")

    def _remote_servers(self) -> List[McpServerConfig]:
        return [
            s
            for s in self.config.enabled_mcp_servers()
            if s.type == "remote" and s.url
        ]

    def start(self) -> None:
        if self._started:
            return
        self._closing = False
        servers = self._remote_servers()
        self._started = True
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True
        if not servers:
            return
        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(servers), self._loop)
        try:
            fut.result(timeout=_START_WAIT_S)
        except concurrent.futures.TimeoutError:
            pass
        except Exception as exc:
            self._errors.append(f"mcp startup failed: {_exc_message(exc)}")
        self._start_retry_loop()

    def reload(self, config: Optional[Config] = None) -> None:
        """Disconnect all MCP servers and reconnect (hot reload)."""
        if config is not None:
            self.config = config
        self._errors = []
        servers = self._remote_servers()
        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._reload_all(servers), self._loop)
        per = self._per_server_timeout_s()
        try:
            fut.result(timeout=max(10.0, per * max(1, len(servers)) + 10.0))
        except Exception as exc:
            self._errors.append(f"mcp reload failed: {_exc_message(exc)}")
        self._started = True
        self._closing = False
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True
        self._start_retry_loop()

    def close(self) -> None:
        self._closing = True
        if not self._loop or not self._thread:
            self._started = False
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_loop(), self._loop)
            fut.result(timeout=10)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._started = False
        self._retry_task = None

    def call_tool(self, qualified_name: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """Call a tool by qualified name. Returns (text, is_error)."""
        info = self._tools.get(qualified_name)
        if info is None:
            return f"unknown mcp tool: {qualified_name}", True
        session = self._sessions.get(info.server)
        if session is None:
            return f"mcp server not connected: {info.server}", True
        if not self._loop:
            return "mcp event loop not running", True
        request_ms = int(
            self.config.mcp_servers.get(info.server, McpServerConfig(info.server)).timeout.get(
                "request",
                self.config.mcp_timeout.get("request", 120_000),
            )
        )
        fut = asyncio.run_coroutine_threadsafe(
            self._call(session, info.name, arguments or {}),
            self._loop,
        )
        try:
            return fut.result(timeout=max(5.0, request_ms / 1000.0))
        except Exception as exc:
            return f"mcp call failed: {exc}", True

    def is_server_connected(self, name: str) -> bool:
        return name in self._sessions

    # ---- internals ----

    def _ensure_loop(self) -> None:
        if self._loop is not None:
            return
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(_mcp_loop_exception_handler)
            self._loop = loop
            ready.set()
            loop.run_forever()
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

        self._thread = threading.Thread(target=_run, name="sleuth-mcp", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)

    async def _reload_all(self, servers: List[McpServerConfig]) -> None:
        await self._disconnect_all()
        self._connecting.clear()
        self._tools.clear()
        self._agent_cards.clear()
        self._agent_card_servers.clear()
        self._status.clear()
        await self._connect_all(servers)

    async def _shutdown_loop(self) -> None:
        task = self._retry_task
        self._retry_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._disconnect_all()

    def _start_retry_loop(self) -> None:
        if self._loop is None or self._closing:
            return
        if self._retry_interval_s() <= 0:
            return
        asyncio.run_coroutine_threadsafe(self._ensure_retry_loop(), self._loop)

    async def _ensure_retry_loop(self) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            return
        if self._retry_interval_s() <= 0 or self._closing:
            return
        self._retry_task = asyncio.create_task(self._retry_loop(), name="mcp-retry")

    async def _retry_loop(self) -> None:
        """Reconnect servers that were down; leave healthy sessions alone."""
        while not self._closing:
            interval = self._retry_interval_s()
            if interval <= 0:
                return
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            if self._closing:
                return
            try:
                await self._retry_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def _retry_disconnected(self) -> None:
        servers = [
            s
            for s in self._remote_servers()
            if s.name not in self._sessions and s.name not in self._connecting
        ]
        if not servers:
            return
        await self._connect_all(servers)

    async def _connect_all(self, servers: List[McpServerConfig]) -> None:
        """Connect all servers in parallel; each has its own timeout."""
        if not servers:
            return
        await asyncio.gather(*(self._connect_one_guarded(srv) for srv in servers))

    async def _connect_one_guarded(self, srv: McpServerConfig) -> None:
        if srv.name in self._sessions or srv.name in self._connecting:
            return
        self._connecting.add(srv.name)
        per = self._per_server_timeout_s()
        url = srv.url or ""
        try:
            await asyncio.wait_for(self._connect_one(srv), timeout=per)
            self._set_server_error(srv.name, None)
            self._status[srv.name] = McpServerStatus(
                name=srv.name,
                url=url,
                connected=True,
                error=None,
                agent=bool(getattr(srv, "agent", False)),
            )
        except asyncio.TimeoutError:
            msg = f"timed out after {per:.0f}s connecting to {url}"
            self._set_server_error(srv.name, msg)
            try:
                await self._disconnect_one(srv.name)
            except Exception:
                pass
            self._status[srv.name] = McpServerStatus(
                name=srv.name,
                url=url,
                connected=False,
                error=msg,
                agent=bool(getattr(srv, "agent", False)),
            )
        except Exception as exc:
            msg = _exc_message(exc)
            self._set_server_error(srv.name, msg)
            try:
                await self._disconnect_one(srv.name)
            except Exception:
                pass
            self._status[srv.name] = McpServerStatus(
                name=srv.name,
                url=url,
                connected=False,
                error=msg,
                agent=bool(getattr(srv, "agent", False)),
            )
        finally:
            self._connecting.discard(srv.name)

    async def _connect_one(self, srv: McpServerConfig) -> None:
        """Start a dedicated lifetime task so anyio cancel scopes stay on one task.

        ``asyncio.wait_for`` wraps this coroutine in a *different* task. Entering
        the MCP HTTP context manager here and exiting it from the waiter (or from
        ``_disconnect_one``) triggers: Attempted to exit cancel scope in a
        different task than it was entered in.
        """
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        stop = asyncio.Event()
        self._stop[srv.name] = stop

        async def lifetime() -> None:
            try:
                await self._session_lifetime(srv, ready, stop)
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
            finally:
                if not ready.done():
                    ready.set_exception(RuntimeError("mcp session ended before initialize"))

        task = asyncio.create_task(lifetime(), name=f"mcp-{srv.name}")
        self._tasks[srv.name] = task
        try:
            await ready
        except BaseException:
            stopper = asyncio.create_task(self._stop_lifetime(srv.name, cancel=True))
            try:
                await asyncio.shield(stopper)
            except (asyncio.CancelledError, Exception):
                pass
            raise

    async def _session_lifetime(
        self,
        srv: McpServerConfig,
        ready: asyncio.Future,
        stop: asyncio.Event,
    ) -> None:
        try:
            import mcp  # noqa: F401
            try:
                from mcp.client.streamable_http import streamable_http_client as streamable_client
            except ImportError:
                from mcp.client.streamable_http import streamablehttp_client as streamable_client
        except ImportError as exc:
            raise RuntimeError(
                "the 'mcp' package is required for remote tools: pip install mcp"
            ) from exc

        url = srv.url
        assert url
        last_err: Optional[BaseException] = None
        for attempt in ("streamable", "sse"):
            try:
                if attempt == "streamable":
                    await self._hold_streamable(srv, streamable_client, ready, stop)
                else:
                    await self._hold_sse(srv, ready, stop)
                return
            except Exception as exc:
                last_err = exc
                continue
        raise RuntimeError(
            f"could not connect to {url}: {_exc_message(last_err) if last_err else 'unknown'}"
        )

    async def _hold_streamable(
        self,
        srv: McpServerConfig,
        streamable_client: Any,
        ready: asyncio.Future,
        stop: asyncio.Event,
    ) -> None:
        url = srv.url
        headers = dict(srv.headers)
        try:
            cm = streamable_client(url, headers=headers or None)
        except TypeError:
            cm = None
        if cm is not None:
            async with cm as streams:
                await self._hold_session(srv, streams, ready, stop)
            return

        from mcp.shared._httpx_utils import create_mcp_http_client

        http_client = create_mcp_http_client(headers=headers or None)
        await http_client.__aenter__()
        try:
            async with streamable_client(url, http_client=http_client) as streams:
                await self._hold_session(srv, streams, ready, stop)
        finally:
            try:
                await http_client.__aexit__(None, None, None)
            except Exception:
                pass

    async def _hold_sse(
        self, srv: McpServerConfig, ready: asyncio.Future, stop: asyncio.Event
    ) -> None:
        from mcp.client.sse import sse_client

        async with sse_client(srv.url, headers=dict(srv.headers) or None) as streams:
            await self._hold_session(srv, streams, ready, stop)

    async def _hold_session(
        self,
        srv: McpServerConfig,
        streams: Any,
        ready: asyncio.Future,
        stop: asyncio.Event,
    ) -> None:
        from mcp import ClientSession

        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            for tool in listed.tools:
                q = f"{sanitize_name(srv.name)}_{sanitize_name(tool.name)}"
                schema = getattr(tool, "inputSchema", None) or getattr(
                    tool, "input_schema", None
                )
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                self._tools[q] = McpToolInfo(
                    server=srv.name,
                    name=tool.name,
                    qualified=q,
                    description=tool.description or f"MCP tool {tool.name} from {srv.name}",
                    input_schema=schema,
                )
            self._sessions[srv.name] = session
            if getattr(srv, "agent", False):
                await self._maybe_fetch_agent_card(srv, session, listed.tools)
            if not ready.done():
                ready.set_result(True)
            await stop.wait()

    async def _maybe_fetch_agent_card(self, srv: McpServerConfig, session: Any, tools: Any) -> None:
        """Call get_agent_card when present; failures are recorded, tools stay registered."""
        names = {getattr(t, "name", "") for t in (tools or [])}
        if "get_agent_card" not in names:
            self._errors.append(
                f"mcp[{srv.name}]: agent=true but get_agent_card tool not found"
            )
            return
        try:
            text, is_error = await self._call(session, "get_agent_card", {})
            if is_error:
                self._errors.append(f"mcp[{srv.name}]: get_agent_card error: {text}")
                return
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("card is not an object")
            agent_name = str(data.get("name") or "").strip()
            if not agent_name:
                raise ValueError("card missing name")
            data.setdefault("mcp_server", srv.name)
            self._agent_cards[agent_name] = data
            self._agent_card_servers[agent_name] = srv.name
        except Exception as exc:
            self._errors.append(f"mcp[{srv.name}]: get_agent_card failed: {exc}")

    async def _call(self, session: Any, name: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        result = await session.call_tool(name, arguments=arguments)
        is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
        parts: List[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                parts.append(str(block))
        body = "\n".join(parts).strip() or "(empty mcp result)"
        return body, is_error

    async def _stop_lifetime(self, name: str, *, cancel: bool = False) -> None:
        """Ask the server's lifetime task to exit; never __aexit__ from here."""
        stop = self._stop.pop(name, None)
        task = self._tasks.pop(name, None)
        if stop is not None:
            stop.set()
        if task is None:
            return
        if cancel and not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _disconnect_one(self, name: str) -> None:
        self._sessions.pop(name, None)
        drop = [k for k, v in self._tools.items() if v.server == name]
        for k in drop:
            del self._tools[k]
        drop_agents = [a for a, s in self._agent_card_servers.items() if s == name]
        for a in drop_agents:
            self._agent_card_servers.pop(a, None)
            self._agent_cards.pop(a, None)
        await self._stop_lifetime(name, cancel=True)

    async def _disconnect_all(self) -> None:
        names = set(self._tasks) | set(self._sessions) | set(self._stop)
        for name in list(names):
            await self._disconnect_one(name)


_GLOBAL: Optional[McpManager] = None


def get_manager(config: Config) -> McpManager:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = McpManager(config)
        _GLOBAL.start()
    else:
        # Keep singleton config pointer fresh for reload/status reads.
        _GLOBAL.config = config
    return _GLOBAL


def shutdown_manager() -> None:
    global _GLOBAL
    if _GLOBAL is not None:
        _GLOBAL.close()
        _GLOBAL = None
