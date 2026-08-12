"""dd_reply MCP 工具面：挂到 Sleuth。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from .agent_card import load_agent_card
from .config import Settings, get_settings
from .kb import list_lexicon, list_risk_codes, load_kb
from .models import FrameworkRequest
from .pipeline import generate_framework


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
            "Generate a 4-part KYC reply framework for one or more risk codes. "
            "Pass risk_codes_json (JSON array of codes like [\"C001\",\"C003\"]), "
            "the 10 KYC field strings, optional local_paths_json (JSON array of file paths "
            "for tests), and optional invest_id for COS attachments. "
            "Returns JSON with markdown + structured sections. For human reference only."
        ),
    )
    def generate_reply_framework(
        risk_codes_json: str = "[]",
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
        invest_id: str = "",
        report_id: str = "",
        bank_id: str = "",
    ) -> str:
        try:
            codes = json.loads(risk_codes_json) if risk_codes_json else []
        except json.JSONDecodeError:
            codes = [c.strip() for c in risk_codes_json.split(",") if c.strip()]
        try:
            paths = json.loads(local_paths_json) if local_paths_json else []
        except json.JSONDecodeError:
            paths = []
        if not isinstance(paths, list):
            paths = []
        req = FrameworkRequest(
            risk_codes=codes if isinstance(codes, list) else [str(codes)],
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
            invest_id=invest_id,
            report_id=report_id,
            bank_id=bank_id,
        )
        result = generate_framework(req, settings=settings)
        return result.model_dump_json(ensure_ascii=False)

    @server.tool(
        name="lookup_risk_kb",
        description="Lookup risk point knowledge by codes_json JSON array; exact match.",
    )
    def lookup_risk_kb(codes_json: str = "[]") -> str:
        try:
            codes = json.loads(codes_json) if codes_json else []
        except json.JSONDecodeError:
            codes = [c.strip() for c in codes_json.split(",") if c.strip()]
        if not isinstance(codes, list):
            codes = [str(codes)]
        kb = load_kb(settings.kb_path)
        found, missing = kb.lookup_risks([str(c) for c in codes])
        payload = {
            "found": [
                {
                    "code": r.code,
                    "name": r.name,
                    "category": r.category,
                    "questions": r.questions,
                    "answer_logic": r.answer_logic,
                    "materials": r.materials,
                    "conclusion_hints": r.conclusion_hints,
                }
                for r in found
            ],
            "missing": missing,
        }
        return json.dumps(payload, ensure_ascii=False)

    @server.tool(name="list_risk_codes", description="List supported risk point codes in local seed KB (offline catalog).")
    def list_risk_codes_tool() -> str:
        try:
            kb = load_kb(settings.kb_path)
            codes = list_risk_codes(kb)
            note = "local_seed"
        except Exception as exc:  # noqa: BLE001
            codes = []
            note = str(exc)
        return json.dumps(
            {
                "codes": codes,
                "source": note,
                "kb_api_configured": settings.kb_api_configured(),
                "hint": "Production risk knowledge comes from DD_REPLY_KB_API_URL search by question=risk code.",
            },
            ensure_ascii=False,
        )

    @server.tool(
        name="list_lexicon",
        description="List language-guard lexicon rules from local lexicon.json.",
    )
    def list_lexicon_tool() -> str:
        from .kb import load_kb_lexicon_only

        kb = load_kb_lexicon_only(settings.kb_path)
        return json.dumps({"items": list_lexicon(kb)}, ensure_ascii=False)

    @server.tool(name="health", description="dd_reply tool-surface health probe.")
    def health() -> str:
        from .kb import load_kb_lexicon_only, load_local_risk_points

        kb_ok = True
        kb_err = ""
        n_risk = 0
        n_lex = 0
        try:
            lex_kb = load_kb_lexicon_only(settings.kb_path)
            n_lex = len(lex_kb.lexicon)
            try:
                n_risk = len(load_local_risk_points(settings.kb_path))
            except Exception:  # noqa: BLE001
                n_risk = 0
        except Exception as exc:  # noqa: BLE001
            kb_ok = False
            kb_err = str(exc)
        return json.dumps(
            {
                "ok": kb_ok,
                "service": "dd-reply-tools",
                "kb_ok": kb_ok,
                "kb_error": kb_err,
                "risk_point_count": n_risk,
                "local_risk_point_count": n_risk,
                "lexicon_rule_count": n_lex,
                "kb_api_configured": settings.kb_api_configured(),
                "kb_fallback_local": settings.kb_fallback_local,
                "llm_configured": settings.llm_configured(),
                "cos_configured": settings.cos_configured(),
                "agent_card": True,
            },
            ensure_ascii=False,
        )

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
        f"(llm={'on' if settings.llm_configured() else 'off/fallback'})"
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
