"""MCP tool surface for Sleuth. Business work lives in pipeline.py."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from .agent_card import load_agent_card
from .attachments import load_excerpts
from .config import Settings, get_settings
from .hitl import need_input_payload, should_pause
from .kb import register as register_kb
from .output import register as register_output
from .pipeline import check_report as run_check


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


def _report_gaps(
    report_text: str,
    report_json: str,
    refs: Optional[list],
) -> tuple[list[str], dict[str, Any]]:
    excerpts, _skipped = load_excerpts(refs or [])
    filled: dict[str, Any] = {}
    if (report_text or "").strip():
        filled["report_text"] = True
    if (report_json or "").strip():
        filled["report_json"] = True
    if excerpts:
        filled["attachment_excerpts"] = len(excerpts)
    if filled:
        return [], filled
    return (
        ["报告正文 report_text", "结构化 JSON report_json", "会话附件"],
        filled,
    )


def _check_payload(
    settings: Settings,
    *,
    report_text: str,
    report_json: str,
    question: str,
    sleuth_llm_json: str,
    refs: Optional[list] = None,
    proceed_with_gaps: Any = False,
) -> str:
    missing, filled = _report_gaps(report_text, report_json, refs)
    if should_pause(settings.hitl_enabled, missing, proceed_with_gaps):
        return json.dumps(need_input_payload(missing, filled), ensure_ascii=False)
    result = run_check(
        settings,
        report_text=report_text,
        report_json=report_json,
        question=question,
        attachment_refs=refs,
        sleuth_llm_json=sleuth_llm_json,
    )
    return json.dumps(result, ensure_ascii=False)


def _check_description(settings: Settings) -> str:
    parts = [
        "Check a filled due-diligence report (plain text, structured JSON, "
        "and/or session-file excerpts). Returns JSON with score (rubric scale), "
        "findings (with location), sources[], and files[] for the Word report.",
        "Optional sleuth_llm_json is injected by Sleuth; this agent's "
        "DD_CHECK_LLM_* wins when complete.",
    ]
    if settings.attachments_enabled:
        parts.append(
            "Prefer excerpt in attachment_refs_json; do not decrypt SM4."
        )
    if settings.hitl_enabled:
        parts.append(
            "If report_text, report_json, and attachment excerpts are all empty, "
            "returns status=need_input (do not invent a score). List missing and "
            "ask the user with the built-in question tool. Pass "
            "proceed_with_gaps=true only after they say there is nothing more."
        )
    return " ".join(parts)


def _register_check_report(server: Any, settings: Settings) -> None:
    description = _check_description(settings)
    if settings.attachments_enabled:

        @server.tool(name="check_report", description=description)
        def check_report_with_refs(
            report_text: str = "",
            report_json: str = "",
            question: str = "",
            attachment_refs_json: str = "[]",
            proceed_with_gaps: bool = False,
            sleuth_llm_json: str = "",
        ) -> str:
            return _check_payload(
                settings,
                report_text=report_text,
                report_json=report_json,
                question=question,
                sleuth_llm_json=sleuth_llm_json,
                refs=_parse_refs(attachment_refs_json),
                proceed_with_gaps=proceed_with_gaps,
            )

        return

    @server.tool(name="check_report", description=description)
    def check_report(
        report_text: str = "",
        report_json: str = "",
        question: str = "",
        proceed_with_gaps: bool = False,
        sleuth_llm_json: str = "",
    ) -> str:
        return _check_payload(
            settings,
            report_text=report_text,
            report_json=report_json,
            question=question,
            sleuth_llm_json=sleuth_llm_json,
            proceed_with_gaps=proceed_with_gaps,
        )


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
            "Sleuth agent dd_check. Call check_report to inspect a due-diligence report. "
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
        server = ServerCls("dd_check", **ctor_kwargs)
    except TypeError:
        for k in ("host", "port", "streamable_http_path", "stateless_http"):
            ctor_kwargs.pop(k, None)
        server = ServerCls("dd_check", **ctor_kwargs)

    _register_http_health(server, settings)
    _install_auth_middleware(server, settings)

    @server.tool(
        name="get_agent_card",
        description=(
            "Return the dd_check Agent Card (name, prompt, permission, skills) "
            "for Sleuth registration when SLEUTH_MCP_SERVERS entry has agent:true."
        ),
    )
    def get_agent_card() -> str:
        return json.dumps(
            load_agent_card(server_name="ddcheck", settings=settings),
            ensure_ascii=False,
        )

    _register_check_report(server, settings)

    @server.tool(name="health", description="dd_check tool-surface health probe.")
    def health() -> str:
        return json.dumps(health_payload(settings), ensure_ascii=False)

    if settings.kb_enabled:
        register_kb(server, settings)
    if settings.output_enabled:
        register_output(server, settings)

    return server


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="dd_check-mcp")
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
        f"dd_check tool surface listening on http://{host}:{port}{path} "
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
