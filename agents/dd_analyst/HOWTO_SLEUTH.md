# 启用 dd_analyst（独立 Agent，不改 sleuth 业务逻辑）

## 推荐：MCP 注册 Agent（无需拷贝 agent.md）

Sleuth 需已支持 `SLEUTH_MCP_SERVERS` 的 `"agent":true`（拉取 `get_agent_card`）。

```powershell
# 1) 工具面
cd agents\dd_analyst
py -3.12 -m pip install -e ".[mcp]"
py -3.12 -m dd_check.mcp_server

# 2) Sleuth .env（仅 MCP；agent:true 自动注册人设/Skill）
# SLEUTH_MCP_SERVERS={"ddcheck":{"type":"remote","url":"http://127.0.0.1:8791/mcp","agent":true}}

# 3) 对话
cd <sleuth-root>
py -3.12 -m sleuth --agent dd_analyst --yolo
```

**兼容旧用法**：省略 `"agent":true` 时行为与以前相同（只挂工具）；可继续用本地 `agent.md` + `SLEUTH_SKILLS_PATHS`。

## HITL 与 Checkpoint

| 环境变量 | 含义 |
|----------|------|
| `DD_CHECK_HITL=0`（默认） | `run_dd_check` 一次返回完整结果 |
| `DD_CHECK_HITL=1` | 可能 `awaiting_human`；确认后 `resume_dd_check`（**必须**配 checkpoint path） |
| `DD_CHECK_HITL_ON_FAIL_ONLY=1` | 仅存在 FAIL 时才暂停 |
| `DD_CHECK_CHECKPOINT_SQLITE_PATH` | 持久 checkpoint SQLite 文件；先执行 `deploy/ddl_langgraph_checkpoint.sql` |

- `resume` = 从 HITL 暂停点继续；`list_dd_checkpoints` + `rollback_dd_check` = 时间旅行分叉（不回滚 Sleuth 对话）。

## 可选：本地 agent.md

无 MCP Agent 注册时，仍可拷贝到 `.opencode\agent\dd_analyst.md`（离线/兜底）。
