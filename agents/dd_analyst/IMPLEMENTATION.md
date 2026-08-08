# dd_analyst capability — Agent 包内检查能力（非独立产品应用）

路径：`agents/dd_analyst/`。**未修改** `sleuth/`。

- Agent：`agent.md`（也可经 MCP `get_agent_card` 下发）
- Skill：`skills/dd-report-check/`（可随 Agent Card 下发）
- 能力：`dd_check/`（LangGraph + 规则/附件 + 可开关 HITL）
- 工具面：`python -m dd_check.mcp_server`（`get_agent_card` / `run_dd_check` / `resume_dd_check`）

HITL：`DD_CHECK_HITL=1` 时打分后 interrupt。Sleuth 推荐：`SLEUTH_MCP_SERVERS` 带 `"agent":true`。详见 [README.md](README.md)。
