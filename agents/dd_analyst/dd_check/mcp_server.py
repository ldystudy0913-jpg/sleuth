"""dd_analyst 的 MCP 工具面：挂到 Sleuth 的「能力插头」。

Sleuth 经 SLEUTH_MCP_SERVERS 连本服务；业务全在 LangGraph，这里只做参数拼装。

工具一览:
  get_agent_card  → 注册人格（agent:true）
  run_dd_check    → start_check（可能 awaiting_human）
  resume_dd_check → resume_check（HITL 续跑，非回滚）
  list_dd_checkpoints / rollback_dd_check → 时间旅行
  run_dd_batch    → invoke_batch（批量，强制同步）
  describe_graph / health
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from .agent_card import load_agent_card
from .config import Settings, get_settings
from .graph import (
    describe_graph,
    invoke_batch,
    list_checkpoints,
    resume_check,
    rollback_check,
    start_check,
)
from .models import BatchCheckRequest, CheckRequest


def _req_from_args(
    reportId: str = "",
    investId: str = "",
    result: str = "",
    question: str = "",
    busCode: str = "",
    busCodeDesc: str = "",
    currentDateTime: str = "",
    currentDate: str = "",
    custType: str = "",
    approveData: str = "",
    phase: str = "CHECK",
    bankId: str = "",
) -> CheckRequest:
    """把 MCP 扁平参数收成 CheckRequest。"""
    return CheckRequest.model_validate(
        {
            "reportId": reportId,
            "investId": investId,
            "result": result,
            "question": question,
            "busCode": busCode,
            "busCodeDesc": busCodeDesc,
            "currentDateTime": currentDateTime,
            "currentDate": currentDate,
            "custType": custType,
            "approveData": approveData,
            "phase": phase,
            "bankId": bankId,
        }
    )


def build_mcp_server(settings: Optional[Settings] = None):
    """注册全部 MCP 工具并返回可 run_streamable_http 的 server。"""
    from mcp.server.mcpserver.server import MCPServer

    settings = settings or get_settings()

    server = MCPServer(
        "dd-analyst",
        instructions=(
            "Due-diligence report check tools for the dd_analyst agent. "
            "Prefer run_dd_check. When status is awaiting_human, ask the user "
            "then call resume_dd_check with thread_id and decision. "
            "To time-travel: list_dd_checkpoints then rollback_dd_check "
            "(does not roll back Sleuth chat history)."
        ),
    )

    @server.tool(
        name="get_agent_card",
        description=(
            "Return the dd_analyst Agent Card (name, prompt, permission, skills) "
            "for Sleuth registration when SLEUTH_MCP_SERVERS entry has agent:true."
        ),
    )
    def get_agent_card() -> str:
        return json.dumps(load_agent_card(server_name="ddcheck"), ensure_ascii=False)

    @server.tool(
        name="run_dd_check",
        description=(
            "Run due-diligence report check graph (CHECK/RECHECK). "
            "Pass reportId, investId, result (form JSON string), question, busCode, "
            "busCodeDesc, currentDateTime, custType, approveData, phase, bankId. "
            "If HITL is enabled may return status=awaiting_human with thread_id; "
            "then call resume_dd_check. Otherwise returns completed score/findings. "
            "With DD_CHECK_CHECKPOINT_SQLITE_PATH, returns thread_id for list/rollback."
        ),
    )
    def run_dd_check(
        reportId: str = "",
        investId: str = "",
        result: str = "",
        question: str = "",
        busCode: str = "",
        busCodeDesc: str = "",
        currentDateTime: str = "",
        currentDate: str = "",
        custType: str = "",
        approveData: str = "",
        phase: str = "CHECK",
        bankId: str = "",
    ) -> str:
        req = _req_from_args(
            reportId,
            investId,
            result,
            question,
            busCode,
            busCodeDesc,
            currentDateTime,
            currentDate,
            custType,
            approveData,
            phase,
            bankId,
        )
        out = start_check(req, settings)
        return json.dumps(out, ensure_ascii=False)

    @server.tool(
        name="resume_dd_check",
        description=(
            "Resume a HITL-paused check (continue from interrupt; NOT rollback). "
            "Pass thread_id from awaiting_human response, "
            "and decision_json like {\"action\":\"approve\"} or "
            "{\"action\":\"edit_summary\",\"summary\":\"...\"} or "
            "{\"action\":\"reject\",\"feedback\":\"...\"}."
        ),
    )
    def resume_dd_check(thread_id: str, decision_json: str = "{\"action\":\"approve\"}") -> str:
        try:
            decision = json.loads(decision_json) if decision_json else {"action": "approve"}
        except json.JSONDecodeError:
            decision = {"action": decision_json}
        out = resume_check(thread_id, decision, settings)
        return json.dumps(out, ensure_ascii=False)

    @server.tool(
        name="list_dd_checkpoints",
        description=(
            "List LangGraph checkpoints for a thread_id (newest first). "
            "Use before rollback_dd_check. Requires DD_CHECK_CHECKPOINT_SQLITE_PATH."
        ),
    )
    def list_dd_checkpoints(thread_id: str, limit: int = 50) -> str:
        out = list_checkpoints(thread_id, settings, limit=int(limit or 50))
        return json.dumps(out, ensure_ascii=False)

    @server.tool(
        name="rollback_dd_check",
        description=(
            "Time-travel fork: continue the check graph from an earlier checkpoint_id "
            "on the same thread_id. Does not delete history; does not roll back Sleuth chat. "
            "Get checkpoint_id from list_dd_checkpoints."
        ),
    )
    def rollback_dd_check(thread_id: str, checkpoint_id: str) -> str:
        out = rollback_check(thread_id, checkpoint_id, settings)
        return json.dumps(out, ensure_ascii=False)

    @server.tool(
        name="run_check",
        description="Alias of run_dd_check (same arguments and return).",
    )
    def run_check(
        reportId: str = "",
        investId: str = "",
        result: str = "",
        question: str = "",
        busCode: str = "",
        busCodeDesc: str = "",
        currentDateTime: str = "",
        currentDate: str = "",
        custType: str = "",
        approveData: str = "",
        phase: str = "CHECK",
        bankId: str = "",
    ) -> str:
        return run_dd_check(
            reportId=reportId,
            investId=investId,
            result=result,
            question=question,
            busCode=busCode,
            busCodeDesc=busCodeDesc,
            currentDateTime=currentDateTime,
            currentDate=currentDate,
            custType=custType,
            approveData=approveData,
            phase=phase,
            bankId=bankId,
        )

    @server.tool(
        name="run_dd_batch",
        description=(
            "Batch recheck via check graph (always synchronous; skips HITL). "
            "items_json is a JSON array of check payloads; phase defaults to RECHECK."
        ),
    )
    def run_dd_batch(items_json: str, phase: str = "RECHECK", question: str = "") -> str:
        items = json.loads(items_json)
        if not isinstance(items, list):
            raise ValueError("items_json must be a JSON array")
        req = BatchCheckRequest.model_validate(
            {"items": items, "phase": phase, "question": question}
        )
        out = invoke_batch(req, settings)
        return json.dumps(out, ensure_ascii=False)

    @server.tool(name="run_batch", description="Alias of run_dd_batch.")
    def run_batch(items_json: str, phase: str = "RECHECK", question: str = "") -> str:
        return run_dd_batch(items_json=items_json, phase=phase, question=question)

    @server.tool(
        name="describe_graph",
        description="Describe the dd_analyst LangGraph check workflow nodes and edges.",
    )
    def describe_graph_tool() -> str:
        return json.dumps(
            describe_graph(hitl_enabled=bool(settings.hitl_enabled)),
            ensure_ascii=False,
        )

    @server.tool(name="health", description="dd_analyst tool-surface health probe.")
    def health() -> str:
        return json.dumps(
            {
                "ok": True,
                "service": "dd-analyst-tools",
                "hitl_enabled": bool(settings.hitl_enabled),
                "hitl_on_fail_only": bool(settings.hitl_on_fail_only),
                "checkpoint_sqlite_configured": bool(settings.checkpoint_sqlite_path),
                "agent_card": True,
            },
            ensure_ascii=False,
        )

    return server


def main(argv=None) -> int:
    """CLI：`python -m dd_check.mcp_server` 启动 Streamable HTTP。"""
    import argparse

    parser = argparse.ArgumentParser(prog="dd-analyst-tools")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--path", default="/mcp")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.hitl_enabled and not settings.checkpoint_sqlite_path:
        raise SystemExit(
            "DD_CHECK_HITL=1 requires DD_CHECK_CHECKPOINT_SQLITE_PATH "
            "(apply deploy/ddl_langgraph_checkpoint.sql first)"
        )
    host = args.host or settings.mcp_host
    port = args.port if args.port is not None else settings.mcp_port
    path = args.path if args.path.startswith("/") else f"/{args.path}"

    server = build_mcp_server(settings)
    print(
        f"dd_analyst tool surface listening on http://{host}:{port}{path} "
        f"(HITL={'on' if settings.hitl_enabled else 'off'}, "
        f"checkpoint={'on' if settings.checkpoint_sqlite_path else 'off'})"
    )
    asyncio.run(
        server.run_streamable_http_async(
            host=host,
            port=port,
            streamable_http_path=path,
            stateless_http=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
