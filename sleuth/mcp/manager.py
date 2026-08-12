"""MCP client — remote URL (Streamable HTTP) tools for sleuth.

Connect configured servers, list_tools, and call_tool. OAuth and stdio
local servers are out of MVP scope.

Connections are parallel with per-server timeouts so one dead server cannot
block the others. Use ``reload()`` to reconnect after servers come online.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config, McpServerConfig


def sanitize_name(name: str) -> str:
    """Make a safe tool-name fragment for MCP catalog entries."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower() or "mcp"


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
        self._cm: Dict[str, Any] = {}  # async context managers to exit
        self._tools: Dict[str, McpToolInfo] = {}
        self._errors: List[str] = []
        self._status: Dict[str, McpServerStatus] = {}
        self._started = False
        self._agent_cards: Dict[str, dict] = {}  # agent name -> card JSON
        self._agent_card_servers: Dict[str, str] = {}  # agent name -> mcp server name
        self._atexit_registered = False

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

    def _remote_servers(self) -> List[McpServerConfig]:
        return [
            s
            for s in self.config.enabled_mcp_servers()
            if s.type == "remote" and s.url
        ]

    def start(self) -> None:
        if self._started:
            return
        servers = self._remote_servers()
        if not servers:
            self._started = True
            return
        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(servers), self._loop)
        try:
            # gather already bounds each server; allow n * per_server + slack
            per = self._per_server_timeout_s()
            fut.result(timeout=max(10.0, per * max(1, len(servers)) + 5.0))
        except Exception as exc:
            self._errors.append(f"mcp startup failed: {exc}")
        self._started = True
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True

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
            self._errors.append(f"mcp reload failed: {exc}")
        self._started = True
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True

    def close(self) -> None:
        if not self._loop or not self._thread:
            self._started = False
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._disconnect_all(), self._loop)
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
        self._tools.clear()
        self._agent_cards.clear()
        self._agent_card_servers.clear()
        self._status.clear()
        await self._connect_all(servers)

    async def _connect_all(self, servers: List[McpServerConfig]) -> None:
        """Connect all servers in parallel; each has its own timeout."""
        if not servers:
            return
        await asyncio.gather(*(self._connect_one_guarded(srv) for srv in servers))

    async def _connect_one_guarded(self, srv: McpServerConfig) -> None:
        per = self._per_server_timeout_s()
        url = srv.url or ""
        try:
            await asyncio.wait_for(self._connect_one(srv), timeout=per)
            self._status[srv.name] = McpServerStatus(
                name=srv.name,
                url=url,
                connected=True,
                error=None,
                agent=bool(getattr(srv, "agent", False)),
            )
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            self._errors.append(f"mcp[{srv.name}]: {msg}")
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

    async def _connect_one(self, srv: McpServerConfig) -> None:
        try:
            from mcp import ClientSession
            try:
                # mcp >= 2.0
                from mcp.client.streamable_http import streamable_http_client as streamable_client
            except ImportError:
                # mcp 1.x
                from mcp.client.streamable_http import streamablehttp_client as streamable_client
        except ImportError as exc:
            raise RuntimeError(
                "the 'mcp' package is required for remote tools: pip install mcp"
            ) from exc

        url = srv.url
        assert url
        headers = dict(srv.headers)

        # Prefer Streamable HTTP; fall back to SSE on failure.
        last_err: Optional[Exception] = None
        for attempt in ("streamable", "sse"):
            http_client: Any = None
            try:
                if attempt == "streamable":
                    try:
                        # mcp 1.x accepts headers=; mcp 2.0 wants http_client=
                        cm = streamable_client(url, headers=headers or None)
                    except TypeError:
                        from mcp.shared._httpx_utils import create_mcp_http_client

                        http_client = create_mcp_http_client(headers=headers or None)
                        await http_client.__aenter__()
                        cm = streamable_client(url, http_client=http_client)
                    streams = await cm.__aenter__()
                    read, write = streams[0], streams[1]
                else:
                    from mcp.client.sse import sse_client

                    cm = sse_client(url, headers=headers or None)
                    read, write = await cm.__aenter__()

                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()
                self._sessions[srv.name] = session
                self._cm[srv.name] = (cm, session, http_client)

                listed = await session.list_tools()
                for tool in listed.tools:
                    q = f"{sanitize_name(srv.name)}_{sanitize_name(tool.name)}"
                    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
                    if not isinstance(schema, dict):
                        schema = {"type": "object", "properties": {}}
                    self._tools[q] = McpToolInfo(
                        server=srv.name,
                        name=tool.name,
                        qualified=q,
                        description=tool.description or f"MCP tool {tool.name} from {srv.name}",
                        input_schema=schema,
                    )
                # Opt-in Agent Card (does not affect default tool-only MCP)
                if getattr(srv, "agent", False):
                    await self._maybe_fetch_agent_card(srv, session, listed.tools)
                return
            except Exception as exc:
                last_err = exc
                # clean partial
                if srv.name in self._cm:
                    try:
                        await self._disconnect_one(srv.name)
                    except Exception:
                        pass
                elif http_client is not None:
                    try:
                        await http_client.__aexit__(None, None, None)
                    except Exception:
                        pass
                continue
        raise RuntimeError(f"could not connect to {url}: {last_err}")

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

    async def _disconnect_one(self, name: str) -> None:
        pair = self._cm.pop(name, None)
        self._sessions.pop(name, None)
        drop = [k for k, v in self._tools.items() if v.server == name]
        for k in drop:
            del self._tools[k]
        # Drop agent cards registered from this server
        drop_agents = [a for a, s in self._agent_card_servers.items() if s == name]
        for a in drop_agents:
            self._agent_card_servers.pop(a, None)
            self._agent_cards.pop(a, None)
        if not pair:
            return
        # Always stored as (cm, session, http_client); http_client may be None.
        cm, session, http_client = pair
        try:
            await session.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass
        if http_client is not None:
            try:
                await http_client.__aexit__(None, None, None)
            except Exception:
                pass

    async def _disconnect_all(self) -> None:
        for name in list(self._cm.keys()):
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
