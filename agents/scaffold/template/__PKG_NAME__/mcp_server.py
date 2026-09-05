"""MCP tool surface for Sleuth. Business work lives in pipeline.py."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from .agent_card import load_agent_card
from .config import Settings, get_settings
from .hitl import need_input_payload, should_pause
from .kb import register as register_kb
from .output import register as register_output
from .pipeline import ping as run_ping


def health_payload(settings: Settings) -> dict[str, Any]:
    body = settings.as_health()
    body["mcp_port"] = settings.mcp_port
    return body


def mcp_token_ok(path: str, authorization: str, token: str) -> bool:
    """Return True if this HTTP request may proceed."""
    p = (path or "").split("?")[0].rstrip("/") or "/"
    if p == "/health" or p.endswith("/health"):
        return True
    expected = (token or "").strip()
    if not expected:
        return True
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    return auth == expected


def _register_http_health(server: Any, settings: Settings) -> None:
    register = getattr(server, "custom_route", None)
    if not callable(register):
        return

    @register("/health", methods=["GET"])
    async def health_http(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse(health_payload(settings))


def _install_auth_middleware(server: Any, settings: Settings) -> None:
    token = (settings.mcp_token or "").strip()
    if not token:
        return
    orig = getattr(server, "streamable_http_app", None)
    if not callable(orig):
        return

    def wrapped():
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        app = orig()

        class McpTokenMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Any, call_next: Any) -> Any:
                path = str(getattr(request.url, "path", "") or "")
                auth = request.headers.get("authorization") or ""
                if mcp_token_ok(path, auth, token):
                    return await call_next(request)
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        app.add_middleware(McpTokenMiddleware)
        return app

    server.streamable_http_app = wrapped  # type: ignore[method-assign]


def _mcp_server_cls():
    try:
        from mcp.server.mcpserver.server import MCPServer as ServerCls
    except ImportError:
        from mcp.server.fastmcp import FastMCP as ServerCls
    return ServerCls


async def _run_streamable_http(
    server: Any,
    *,
    host: str,
    port: int,
    streamable_http_path: str,
    stateless_http: bool = True,
) -> None:
    try:
        await server.run_streamable_http_async(
            host=host,
            port=port,
            streamable_http_path=streamable_http_path,
            stateless_http=stateless_http,
        )
    except TypeError:
        settings_obj = getattr(server, "settings", None)
        if settings_obj is not None:
            settings_obj.host = host
            settings_obj.port = port
            settings_obj.streamable_http_path = streamable_http_path
            settings_obj.stateless_http = stateless_http
        await server.run_streamable_http_async()


def _parse_refs(attachment_refs_json: str) -> list:
    try:
        refs = json.loads(attachment_refs_json) if attachment_refs_json else []
    except json.JSONDecodeError:
        refs = []
    if not isinstance(refs, list):
        return []
    return [r for r in refs if isinstance(r, dict)]


def _ping_payload(
    settings: Settings,
    message: str,
    *,
    refs: Optional[list] = None,
    proceed_with_gaps: Any = False,
) -> str:
    missing = [] if (message or "").strip() else ["回显文本 message"]
    if should_pause(settings.hitl_enabled, missing, proceed_with_gaps):
        return json.dumps(need_input_payload(missing), ensure_ascii=False)
    if refs is not None:
        result = run_ping(message, attachment_refs=refs)
    else:
        result = run_ping(message)
    return json.dumps(result, ensure_ascii=False)


def _ping_description(settings: Settings) -> str:
    parts = [
        "Echo a message. Replace this with your real business tool.",
        "Optional sleuth_llm_json is injected by Sleuth (session model).",
        "Prefer this agent's __ENV_PREFIX___LLM_* when those three are set.",
    ]
    if settings.attachments_enabled:
        parts.append(
            "Optional attachment_refs_json is injected by Sleuth "
            "(session-file excerpts). Prefer excerpt; do not decrypt SM4."
        )
    if settings.hitl_enabled:
        parts.append(
            "If message is empty, returns status=need_input (do not invent). "
            "List missing and ask the user with the built-in question tool. "
            "Pass proceed_with_gaps=true only after they say there is nothing more."
        )
    return " ".join(parts)


def _register_ping(server: Any, settings: Settings) -> None:
    description = _ping_description(settings)
    if settings.attachments_enabled:

        @server.tool(name="ping", description=description)
        def ping_with_refs(
            message: str = "pong",
            attachment_refs_json: str = "[]",
            proceed_with_gaps: bool = False,
            sleuth_llm_json: str = "",
        ) -> str:
            del sleuth_llm_json
            return _ping_payload(
                settings,
                message,
                refs=_parse_refs(attachment_refs_json),
                proceed_with_gaps=proceed_with_gaps,
            )

        return

    @server.tool(name="ping", description=description)
    def ping(
        message: str = "pong",
        proceed_with_gaps: bool = False,
        sleuth_llm_json: str = "",
    ) -> str:
        del sleuth_llm_json
        return _ping_payload(settings, message, proceed_with_gaps=proceed_with_gaps)


def build_mcp_server(
    settings: Optional[Settings] = None,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    streamable_http_path: str = "/mcp",
    stateless_http: bool = True,
):
    ServerCls = _mcp_server_cls()
    settings = settings or get_settings()

    ctor_kwargs: dict = {
        "instructions": (
            "Sleuth agent __AGENT_NAME__. Call ping to echo a message. "
            "Use get_agent_card only when Sleuth registers this process with agent:true."
        ),
    }
    if host is not None:
        ctor_kwargs["host"] = host
    if port is not None:
        ctor_kwargs["port"] = port
    ctor_kwargs["streamable_http_path"] = streamable_http_path
    ctor_kwargs["stateless_http"] = stateless_http

    try:
        server = ServerCls("__PKG_NAME__", **ctor_kwargs)
    except TypeError:
        for k in ("host", "port", "streamable_http_path", "stateless_http"):
            ctor_kwargs.pop(k, None)
        server = ServerCls("__PKG_NAME__", **ctor_kwargs)

    _register_http_health(server, settings)
    _install_auth_middleware(server, settings)

    @server.tool(
        name="get_agent_card",
        description=(
            "Return the __AGENT_NAME__ Agent Card (name, prompt, permission, skills) "
            "for Sleuth registration when SLEUTH_MCP_SERVERS entry has agent:true."
        ),
    )
    def get_agent_card() -> str:
        return json.dumps(
            load_agent_card(server_name="__SERVER_NAME__", settings=settings),
            ensure_ascii=False,
        )

    _register_ping(server, settings)

    @server.tool(name="health", description="__AGENT_NAME__ tool-surface health probe.")
    def health() -> str:
        return json.dumps(health_payload(settings), ensure_ascii=False)

    if settings.kb_enabled:
        register_kb(server, settings)
    if settings.output_enabled:
        register_output(server, settings)

    return server


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="__PKG_NAME__-mcp")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--path", default="/mcp")
    args = parser.parse_args(argv)

    settings = get_settings()
    host = args.host or settings.mcp_host
    port = args.port if args.port is not None else settings.mcp_port
    path = args.path if args.path.startswith("/") else f"/{args.path}"

    server = build_mcp_server(
        settings,
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
    )
    print(
        f"__AGENT_NAME__ tool surface listening on http://{host}:{port}{path} "
        f"(health GET http://{host}:{port}/health)"
    )
    asyncio.run(
        _run_streamable_http(
            server,
            host=host,
            port=port,
            streamable_http_path=path,
            stateless_http=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
