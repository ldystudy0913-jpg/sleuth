"""MCP 工具面：只做注册、鉴权、入参拼装；业务在 pipeline.py。

二次开发时改这里：
1. 把 `_register_ping` 换成你的 `@server.tool`（或留 ping 当探活）。
2. HITL：在包装函数里自己列 `missing`（`_ping_payload` 的「空 message」只是演示）。
3. 附件：新工具必须自己声明 `attachment_refs_json`，开 ATTACHMENTS 不会自动给新工具注入。
4. `sleuth_llm_json`：要用会话模型时入参里保留这个字段，在 pipeline 里交给 llm.py。
不要在本文件里调上游 API 或阻塞等人；人介入返回 need_input 后立刻结束本次 tools/call。
"""
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
    """探活 JSON：含各可选能力是否已按 env 打开（attachments / hitl / kb / llm）。"""
    body = settings.as_health()
    body["mcp_port"] = settings.mcp_port
    return body


def mcp_token_ok(path: str, authorization: str, token: str) -> bool:
    """校验 Bearer。`/health` 始终放行；token 为空则全部放行。"""
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
    """注册 GET /health（绕过 MCP 握手，给探活和网关用）。二次开发一般不用改。"""
    register = getattr(server, "custom_route", None)
    if not callable(register):
        return

    @register("/health", methods=["GET"])
    async def health_http(_request: Any) -> Any:
        """HTTP GET /health，不校验 MCP token。"""
        from starlette.responses import JSONResponse

        return JSONResponse(health_payload(settings))


def _install_auth_middleware(server: Any, settings: Settings) -> None:
    """MCP_TOKEN 非空时给 Streamable HTTP 装鉴权。只配 env，不必二次开发。"""
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
                """非 /health 请求校验 Bearer。"""
                path = str(getattr(request.url, "path", "") or "")
                auth = request.headers.get("authorization") or ""
                if mcp_token_ok(path, auth, token):
                    return await call_next(request)
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        app.add_middleware(McpTokenMiddleware)
        return app

    server.streamable_http_app = wrapped  # type: ignore[method-assign]


def _mcp_server_cls():
    """兼容行内 MCPServer 与开源 FastMCP。二次开发不用改。"""
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
    """启动 Streamable HTTP。不同 MCP 包的 run 签名不一致，这里做兼容。"""
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
    """把 Sleuth 注入的 attachment_refs_json 解成 dict 列表。坏 JSON 当空列表。"""
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
    """演示：缺料则返回 need_input，否则进 pipeline.ping。

    `missing` 在这里用「message 是否为空」判断，**只为跑通 HITL**。
    真实业务请复制本函数结构，把 missing 改成你的字段/附件规则，例如：
    missing = []
    if not report_text.strip() and not refs:
        missing.append("报告正文或会话附件")
    开 HITL=1 不会自动知道这些规则。基座不解析返回的 JSON，要靠 SOP 调 question。
    """
    missing = [] if (message or "").strip() else ["回显文本 message"]
    if should_pause(settings.hitl_enabled, missing, proceed_with_gaps):
        return json.dumps(need_input_payload(missing), ensure_ascii=False)
    from .progress import bind_current

    progress_fn = bind_current()
    if refs is not None:
        result = run_ping(message, attachment_refs=refs, progress_fn=progress_fn)
    else:
        result = run_ping(message, progress_fn=progress_fn)
    return json.dumps(result, ensure_ascii=False)


def _ping_description(settings: Settings) -> str:
    """按已打开的能力拼 ping 的 tool description，给模型看何时调用。"""
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
    """注册演示工具 ping。你的主工具照这个模式写：声明入参 → 算 missing → 调 pipeline。

    ATTACHMENTS=1 时才会声明 attachment_refs_json；新工具不会自动带上这个参数。
    sleuth_llm_json 始终声明，避免未配本包 LLM 时无法用会话模型。
    """
    description = _ping_description(settings)
    if settings.attachments_enabled:

        @server.tool(name="ping", description=description)
        def ping_with_refs(
            message: str = "pong",
            attachment_refs_json: str = "[]",
            proceed_with_gaps: bool = False,
            sleuth_llm_json: str = "",
        ) -> str:
            """演示 ping（已开附件）。sleuth_llm_json 由基座注入，演示不调 LLM 故丢弃。"""
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
        """演示 ping（未开附件）。新业务工具请另写，不要只改 message 判断。"""
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
    """组装 FastMCP/MCPServer：health、鉴权、log_trace、ping、可选 kb/emit_file。

    二次开发：在 `_register_ping` 之后加你的 `@server.tool`，并在 agent.md 写合格名 allow。
    """
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
            "Return the __AGENT_NAME__ Agent Card (name, prompt, permission, skills) "
            "for Sleuth registration when SLEUTH_MCP_SERVERS entry has agent:true."
        ),
    )
    def get_agent_card() -> str:
        """Sleuth agent:true 时拉取 Card。不要在这里改人设。"""
        return json.dumps(
            load_agent_card(server_name="__SERVER_NAME__", settings=settings),
            ensure_ascii=False,
        )

    _register_ping(server, settings)

    @server.tool(name="health", description="__AGENT_NAME__ tool-surface health probe.")
    def health() -> str:
        """MCP 工具探活，字段与 GET /health 一致。"""
        return json.dumps(health_payload(settings), ensure_ascii=False)

    if settings.kb_enabled:
        register_kb(server, settings)
    if settings.output_enabled:
        register_output(server, settings)

    return server


def main(argv=None) -> int:
    """命令行入口：读本包 .env 的 host/port，监听 Streamable HTTP。"""
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
