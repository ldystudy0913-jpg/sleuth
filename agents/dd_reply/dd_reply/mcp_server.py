"""dd_reply MCP 工具面：挂到 Sleuth。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from .agent_card import load_agent_card
from .config import Settings, get_settings
from .kb import list_lexicon, load_kb_lexicon_only
from .kb.remote import retrieve_risk_codes
from .models import FrameworkRequest
from .pipeline import generate_framework


def health_payload(settings: Settings) -> dict[str, Any]:
    """Shared liveness JSON for GET /health and the MCP ``health`` tool."""
    kb_ok = True
    kb_err = ""
    n_lex = 0
    try:
        lex_kb = load_kb_lexicon_only(settings.kb_path)
        n_lex = len(lex_kb.lexicon)
    except Exception as exc:  # noqa: BLE001
        kb_ok = False
        kb_err = str(exc)
    return {
        "ok": kb_ok,
        "service": "dd-reply-tools",
        "kb_ok": kb_ok,
        "kb_error": kb_err,
        "lexicon_rule_count": n_lex,
        "kb_api_configured": settings.kb_api_configured(),
        "kb_sort_count": int(getattr(settings, "kb_sort_count", 10) or 0),
        "llm_configured": settings.llm_configured(),
        "cos_configured": settings.cos_configured(),
        "agent_card": True,
    }


def _register_http_health(server: Any, settings: Settings) -> None:
    """Expose GET /health on the Streamable HTTP app (Docker / k8s probes)."""
    register = getattr(server, "custom_route", None)
    if not callable(register):
        return

    @register("/health", methods=["GET"])
    async def health_http(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse(health_payload(settings))


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
            "KYC due-diligence reply framework generator. "
            "Use generate_reply_framework with risk_codes + 10 KYC fields. "
            "Output is for human analysts; never claim auto-approval."
        ),
    }
    if host is not None:
        ctor_kwargs["host"] = host
    if port is not None:
        ctor_kwargs["port"] = port
    ctor_kwargs["streamable_http_path"] = streamable_http_path
    ctor_kwargs["stateless_http"] = stateless_http

    try:
        server = ServerCls("dd-reply", **ctor_kwargs)
    except TypeError:
        for k in ("host", "port", "streamable_http_path", "stateless_http"):
            ctor_kwargs.pop(k, None)
        server = ServerCls("dd-reply", **ctor_kwargs)

    _register_http_health(server, settings)

    @server.tool(
        name="get_agent_card",
        description=(
            "Return the dd_reply Agent Card (name, prompt, permission, skills) "
            "for Sleuth registration when SLEUTH_MCP_SERVERS entry has agent:true."
        ),
    )
    def get_agent_card() -> str:
        return json.dumps(load_agent_card(server_name="ddreply"), ensure_ascii=False)

    @server.tool(
        name="generate_reply_framework",
        description=(
            "Generate a 4-part KYC reply framework. "
            "Pass risk_codes_json (JSON array of codes like [\"C001\"]) and/or "
            "risk_names_json (JSON array of names like [\"行政处罚记录\"]). "
            "Each item is used as the KB search question. Also pass the 10 KYC field "
            "strings, optional attachment_refs_json (session mailbox HTTPS refs; "
            "preferred in production), optional local_paths_json (local tests only), "
            "optional invest_id. If fields are missing, returns status=need_input "
            "(do not generate yet — list missing fields and ask the user whether they "
            "have more to provide). Pass proceed_with_gaps=true only after the user "
            "says there is nothing more and analysis should continue. "
            "Returns JSON with markdown + structured sections. For human reference only."
        ),
    )
    def generate_reply_framework(
        risk_codes_json: str = "[]",
        risk_names_json: str = "[]",
        customer_name: str = "",
        established_at: str = "",
        business_scope: str = "",
        employee_count: str = "",
        registered_capital: str = "",
        annual_revenue: str = "",
        ubo_info: str = "",
        main_business: str = "",
        account_purpose: str = "",
        tx_pattern_estimate: str = "",
        local_paths_json: str = "[]",
        attachment_refs_json: str = "[]",
        invest_id: str = "",
        report_id: str = "",
        bank_id: str = "",
        proceed_with_gaps: bool = False,
    ) -> str:
        try:
            codes = json.loads(risk_codes_json) if risk_codes_json else []
        except json.JSONDecodeError:
            codes = [c.strip() for c in risk_codes_json.split(",") if c.strip()]
        try:
            names = json.loads(risk_names_json) if risk_names_json else []
        except json.JSONDecodeError:
            names = [c.strip() for c in risk_names_json.split(",") if c.strip()]
        try:
            paths = json.loads(local_paths_json) if local_paths_json else []
        except json.JSONDecodeError:
            paths = []
        if not isinstance(paths, list):
            paths = []
        try:
            refs = json.loads(attachment_refs_json) if attachment_refs_json else []
        except json.JSONDecodeError:
            refs = []
        if not isinstance(refs, list):
            refs = []
        req = FrameworkRequest(
            risk_codes=codes if isinstance(codes, list) else [str(codes)],
            risk_names=names if isinstance(names, list) else [str(names)],
            customer_name=customer_name,
            established_at=established_at,
            business_scope=business_scope,
            employee_count=employee_count,
            registered_capital=registered_capital,
            annual_revenue=annual_revenue,
            ubo_info=ubo_info,
            main_business=main_business,
            account_purpose=account_purpose,
            tx_pattern_estimate=tx_pattern_estimate,
            local_paths=[str(p) for p in paths],
            attachment_refs=[r for r in refs if isinstance(r, dict)],
            invest_id=invest_id,
            report_id=report_id,
            bank_id=bank_id,
        )
        gaps = proceed_with_gaps
        if isinstance(gaps, str):
            gaps = gaps.strip().lower() in ("1", "true", "yes", "on")
        if req.missing_inputs() and not gaps:
            return json.dumps(req.need_input_payload(), ensure_ascii=False)
        try:
            result = generate_framework(req, settings=settings)
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return result.model_dump_json(ensure_ascii=False)

    @server.tool(
        name="lookup_risk_kb",
        description="Lookup risk-point knowledge via the remote KB API. codes_json may contain codes (C001) and/or names.",
    )
    def lookup_risk_kb(codes_json: str = "[]") -> str:
        try:
            codes = json.loads(codes_json) if codes_json else []
        except json.JSONDecodeError:
            codes = [c.strip() for c in codes_json.split(",") if c.strip()]
        if not isinstance(codes, list):
            codes = [str(codes)]
        if not settings.kb_api_configured():
            return json.dumps(
                {
                    "error": "DD_REPLY_KB_API_URL is required; risk-point knowledge is remote-only",
                    "found": [],
                    "missing": [str(c).strip().upper() for c in codes if str(c).strip()],
                },
                ensure_ascii=False,
            )
        retrievals = retrieve_risk_codes([str(c) for c in codes], settings)
        found = []
        missing = []
        for r in retrievals:
            if r.ok:
                found.append(
                    {
                        "code": r.code,
                        "hit_count": len(r.hits),
                        "sources": [h.source_cite() for h in r.hits],
                        "hits": [
                            {
                                "title": h.title,
                                "file_name": h.file_name,
                                "url": h.source_url(),
                                "knowledge_id": h.knowledge_id,
                                "rank_score": h.rank_score,
                                "paragraph": h.paragraph,
                            }
                            for h in r.hits
                        ],
                    }
                )
            else:
                missing.append({"code": r.code, "error": r.error or "empty_hits"})
        return json.dumps({"found": found, "missing": missing}, ensure_ascii=False)

    @server.tool(
        name="list_risk_codes",
        description="Risk codes are not listed locally; production knowledge is searched via KB API.",
    )
    def list_risk_codes_tool() -> str:
        return json.dumps(
            {
                "codes": [],
                "source": "remote_api",
                "kb_api_configured": settings.kb_api_configured(),
                "hint": "Risk knowledge is searched per code via DD_REPLY_KB_API_URL; there is no local catalog.",
            },
            ensure_ascii=False,
        )

    @server.tool(
        name="list_lexicon",
        description="List language-guard lexicon rules from local lexicon.json.",
    )
    def list_lexicon_tool() -> str:
        kb = load_kb_lexicon_only(settings.kb_path)
        return json.dumps({"items": list_lexicon(kb)}, ensure_ascii=False)

    @server.tool(name="health", description="dd_reply tool-surface health probe.")
    def health() -> str:
        return json.dumps(health_payload(settings), ensure_ascii=False)

    return server


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="dd-reply-tools")
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
        f"dd_reply tool surface listening on http://{host}:{port}{path} "
        f"(health GET http://{host}:{port}/health; "
        f"llm={'on' if settings.llm_configured() else 'off/fallback'})"
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
