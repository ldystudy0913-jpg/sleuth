"""MCP tool surface for Sleuth. Business work lives in pipeline.py."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from .agent_card import load_agent_card
from .attachments import load_excerpts
from .bizerror import APPError
from .config import Settings, get_settings
from .envelope import json_app
from .hitl import need_input_payload, should_pause
from .http_routes import (
    delete_check_payload,
    list_checks_payload,
    register_check_routes,
    scenarios_payload,
)
from .kb import register as register_kb
from .output import register as register_output
from .pipeline import check_report as run_check
from .scenarios import public_catalog, resolve_pack


def health_payload(settings: Settings) -> dict[str, Any]:
    body = settings.as_health()
    body["mcp_port"] = settings.mcp_port
    try:
        body["scenario_count"] = len(public_catalog(settings.config_dir))
    except Exception:
        body["scenario_count"] = 0
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

        from .bizerror import APPError, BizErrorCode

        app = orig()

        class McpTokenMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Any, call_next: Any) -> Any:
                path = str(getattr(request.url, "path", "") or "")
                auth = request.headers.get("authorization") or ""
                if mcp_token_ok(path, auth, token):
                    return await call_next(request)
                return json_app(APPError.of(BizErrorCode.AUTH_NOT_PERMIT, status=401))

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
        ["\u62a5\u544a\u6b63\u6587 report_text", "\u7ed3\u6784\u5316 JSON report_json", "\u4f1a\u8bdd\u9644\u4ef6"],
        filled,
    )


def _collect_gaps(
    settings: Settings,
    *,
    report_text: str,
    report_json: str,
    refs: Optional[list],
    report_id: str,
    scenario: str,
    question: str,
    proceed_with_gaps: Any,
) -> tuple[list[str], dict[str, Any], Optional[Any]]:
    missing, filled = _report_gaps(report_text, report_json, refs)
    filled["scenarios"] = public_catalog(settings.config_dir)
    if not (report_id or "").strip():
        missing.append("\u4e1a\u52a1\u5c3d\u8c03\u62a5\u544a id report_id")
    pack = resolve_pack(
        settings.config_dir,
        scenario=scenario,
        question=question,
        proceed_with_gaps=bool(proceed_with_gaps),
        hitl_enabled=settings.hitl_enabled,
    )
    if pack is not None:
        filled["scenario"] = pack.scenario_id
    elif settings.hitl_enabled:
        missing.append("\u68c0\u67e5\u573a\u666f")
    return missing, filled, pack


def _tool_error(exc: APPError) -> str:
    return json.dumps(
        {"ok": False, "code": exc.code, "msg": exc.msg},
        ensure_ascii=False,
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
    report_id: str = "",
    scenario: str = "",
) -> str:
    missing, filled, pack = _collect_gaps(
        settings,
        report_text=report_text,
        report_json=report_json,
        refs=refs,
        report_id=report_id,
        scenario=scenario,
        question=question,
        proceed_with_gaps=proceed_with_gaps,
    )
    if should_pause(settings.hitl_enabled, missing, proceed_with_gaps):
        return json.dumps(need_input_payload(missing, filled), ensure_ascii=False)
    from .progress import bind_current

    scenario_id = pack.scenario_id if pack is not None else "default"
    result = run_check(
        settings,
        report_text=report_text,
        report_json=report_json,
        question=question,
        attachment_refs=refs,
        sleuth_llm_json=sleuth_llm_json,
        scenario=scenario_id,
        report_id=report_id,
        progress_fn=bind_current(),
    )
    return json.dumps(result, ensure_ascii=False)


def _check_description(settings: Settings) -> str:
    parts = [
        "Check a filled due-diligence report. Pass scenario (id or alias) and report_id. "
        "Returns JSON with findings or conflicts, sources[], and files[] for the Word report. "
        "Optional sleuth_llm_json is injected by Sleuth; this agent's DD_CHECK_LLM_* wins when complete.",
    ]
    if settings.attachments_enabled:
        parts.append("Prefer excerpt in attachment_refs_json; do not decrypt SM4.")
    if settings.hitl_enabled:
        parts.append(
            "If materials, report_id, or scenario are missing, returns status=need_input. "
            "List missing and ask with the built-in question tool (once for scenario). "
            "Pass proceed_with_gaps=true after they say continue/default; empty scenario then uses default."
        )
    return " ".join(parts)


def _register_check_report(server: Any, settings: Settings) -> None:
    description = _check_description(settings)

    def _run(
        report_text: str = "",
        report_json: str = "",
        question: str = "",
        proceed_with_gaps: bool = False,
        sleuth_llm_json: str = "",
        report_id: str = "",
        scenario: str = "",
        attachment_refs_json: str = "[]",
    ) -> str:
        refs = _parse_refs(attachment_refs_json) if settings.attachments_enabled else None
        return _check_payload(
            settings,
            report_text=report_text,
            report_json=report_json,
            question=question,
            sleuth_llm_json=sleuth_llm_json,
            refs=refs,
            proceed_with_gaps=proceed_with_gaps,
            report_id=report_id,
            scenario=scenario,
        )

    if settings.attachments_enabled:

        @server.tool(name="check_report", description=description)
        def check_report_with_refs(
            report_text: str = "",
            report_json: str = "",
            question: str = "",
            attachment_refs_json: str = "[]",
            proceed_with_gaps: bool = False,
            sleuth_llm_json: str = "",
            report_id: str = "",
            scenario: str = "",
        ) -> str:
            return _run(
                report_text=report_text,
                report_json=report_json,
                question=question,
                proceed_with_gaps=proceed_with_gaps,
                sleuth_llm_json=sleuth_llm_json,
                report_id=report_id,
                scenario=scenario,
                attachment_refs_json=attachment_refs_json,
            )

        return

    @server.tool(name="check_report", description=description)
    def check_report(
        report_text: str = "",
        report_json: str = "",
        question: str = "",
        proceed_with_gaps: bool = False,
        sleuth_llm_json: str = "",
        report_id: str = "",
        scenario: str = "",
    ) -> str:
        return _run(
            report_text=report_text,
            report_json=report_json,
            question=question,
            proceed_with_gaps=proceed_with_gaps,
            sleuth_llm_json=sleuth_llm_json,
            report_id=report_id,
            scenario=scenario,
        )


def _register_history_tools(server: Any, settings: Settings) -> None:
    @server.tool(
        name="list_scenarios",
        description=(
            "List due-diligence check scenarios (id, title, description, aliases). "
            "Call this when the user asks which reports or scenes can be checked. Do not invent names."
        ),
    )
    def list_scenarios() -> str:
        try:
            return json.dumps({"ok": True, **scenarios_payload(settings)}, ensure_ascii=False)
        except APPError as exc:
            return _tool_error(exc)

    @server.tool(
        name="list_checks",
        description=(
            "List past checks for a report_id. Returns metadata only "
            "(check_id, scenario, filename, created_at); no Word bytes."
        ),
    )
    def list_checks(report_id: str = "") -> str:
        try:
            body = list_checks_payload(settings, report_id, include_files=False)
            return json.dumps({"ok": True, **body}, ensure_ascii=False)
        except APPError as exc:
            return _tool_error(exc)

    @server.tool(
        name="delete_check",
        description="Delete one history check by check_id (row + COS object). Does not remove session files.",
    )
    def delete_check(check_id: str = "") -> str:
        try:
            body = delete_check_payload(settings, check_id)
            return json.dumps({"ok": True, **body}, ensure_ascii=False)
        except APPError as exc:
            return _tool_error(exc)


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
            "Call list_scenarios when asked which scenes can be checked. "
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
    register_check_routes(server, settings)
    _install_auth_middleware(server, settings)
    try:
        from .logtrace import ensure_initialized, install_mcp_middleware, is_enabled
    except ImportError:
        is_enabled = lambda: False  # type: ignore
    if is_enabled():
        ensure_initialized()
        install_mcp_middleware(server)

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
    _register_history_tools(server, settings)

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
