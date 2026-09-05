# Agent / MCP 编排模式

> 横切能力（ACL、记忆、HITL、KB、文件 SM4 邮箱）在所有模式下不变；编排只改变「谁决定调哪个工具、调几次」。  
> 所有开关与默认值来自 **`.env` / `opencode.jsonc` 的 `orchestration` 块 / Agent Card**，不在业务代码里写死。

---

## 1. 模式一览

| 模式 | 标识 | 谁触发 | 说明 |
|------|------|--------|------|
| **A Host** | `host` | 默认 | 基座 LLM 选工具、填参、归纳（现状） |
| **C Pipeline** | `pipeline` | Card 默认 + SOP | MCP 内多步；SOP 要求**一次**调 `primary_tool` |
| **F Auto-invoke** | `auto_invoke` | HTTP `auto_run` / CLI `--auto-run` | 跳过首轮基座推理，直调 `primary_tool` |
| **F Invoke** | `invoke` | HTTP `invoke: {tool, args}` | 显式直调指定工具 |
| **B Delegate** | `delegate` | `task(subagent_type=…)` | 子 Session 自治；需 Card `delegatable: true` + agent grant |
| **D Async** | `async` | HTTP `execution: async` | 后台 job；需 `SLEUTH_ORCHESTRATION_ASYNC_ENABLED=1` |
| **E Parallel** | `parallel` | HTTP `parallel: [...]` | 多专岗扇出；每路独立 ACL |
| **G Handoff** | — | 工具 `handoff_refs_json`（规划） | 邮箱 `file_id` 链式传递 |

---

## 2. 模式选择优先级（高 → 低）

1. **单次 HTTP 请求体**：`orchestration` / `auto_run` / `invoke` / `execution` / `parallel`
2. **会话 metadata**（键名可配，默认 `orchestration`）
3. **Agent Card**：`orchestration` / `execution`
4. **全局默认**：`SLEUTH_ORCHESTRATION_DEFAULT_MODE`（默认 `host`）

ACL **不受**模式字段影响：仍先 `assert_resource_allowed` + `session_acl_error`。

---

## 3. Agent Card 字段

| 字段 | 说明 |
|------|------|
| `orchestration` | `host` \| `pipeline` \| `delegate` \| `async`（建议 `dd_check` 出厂 `pipeline`） |
| `primary_tool` | pipeline / auto_run 默认一次调用的合格工具名 |
| `delegatable` | 是否允许 `task` 委托（**不**替代 agent grant） |
| `execution` | `sync` \| `async`（默认取全局 `default_execution`） |
| `auto_invoke_prompt_field` | auto_run 时用户 prompt 写入的工具参数字段名 |
| `auto_invoke_args` | auto_run 静态默认参数（object） |

`dd_check` 示例见 [`agents/dd_check/agent.md`](../agents/dd_check/agent.md)。

---

## 4. 全局配置（`.env` / JSONC）

```env
# 默认模式与执行方式
SLEUTH_ORCHESTRATION_DEFAULT_MODE=host
SLEUTH_ORCHESTRATION_DEFAULT_EXECUTION=sync
SLEUTH_ORCHESTRATION_DEFAULT_DELEGATABLE=0

# 功能开关
SLEUTH_ORCHESTRATION_INVOKE_ENABLED=1
SLEUTH_ORCHESTRATION_AUTO_RUN_ENABLED=1
SLEUTH_ORCHESTRATION_PARALLEL_ENABLED=1
SLEUTH_ORCHESTRATION_ASYNC_ENABLED=0
SLEUTH_ORCHESTRATION_DELEGATE_ENABLED=1

# 限制
SLEUTH_ORCHESTRATION_PARALLEL_MAX_BRANCHES=8
SLEUTH_ORCHESTRATION_ASYNC_MAX_JOBS_PER_USER=32

# 整块 JSON 覆盖（与上表单项合并，JSON 优先于逐项 env 的顺序见 config 加载）
# SLEUTH_ORCHESTRATION={"default_mode":"host","async_enabled":false}
```

`opencode.jsonc` 等价：

```jsonc
{
  "orchestration": {
    "default_mode": "host",
    "auto_run_enabled": true,
    "async_enabled": false
  }
}
```

---

## 5. HTTP 示例

```json
// Mode A（默认）
{ "agent": "dd_check", "prompt": "请检查这份报告", "file_ids": ["..."] }

// Mode F — 一键检查
{ "agent": "dd_check", "auto_run": true, "prompt": "...", "file_ids": ["..."] }

// Mode F — 参数已齐
{ "agent": "dd_check", "invoke": { "tool": "ddcheck_check_report", "args": { "report_text": "..." } } }

// Mode D — 异步（需开启 async_enabled）
{ "agent": "dd_check", "execution": "async", "auto_run": true, "prompt": "..." }

// Mode E — 并行（用户对两 agent 均有 grant）
{ "agent": "build", "parallel": [
  { "agent": "dd_check", "prompt": "检查报告", "file_ids": ["..."] },
  { "agent": "dd_reply", "prompt": "生成框架", "args": { "risk_codes_json": "[\"C001\"]" } }
]}
```

异步 job 状态：`GET /v1/orchestration/jobs/{job_id}`

CLI：`sleuth --agent dd_check --auto-run "检查这份报告"`

---

## 6. 必经 Session 装配层（禁止裸调 MCP）

新模式实现 **必须** 经过下列检查（见 [`sleuth/orchestration.py`](../sleuth/orchestration.py) → `Session.execute_guarded_tool` → `McpBridgeTool`）：

| # | 检查 | 实现点 |
|---|------|--------|
| 1 | 用户身份 | `session.user_id`；子 Session / job **继承**主会话 user |
| 2 | Agent grant | `assert_resource_allowed(user, agent)`；`build_session` / `task` / parallel 每路 |
| 3 | Skill grant | `set_skills` / `skill` 工具（不变） |
| 4 | 会话 agent 与 MCP owner | `session_may_use_owner_agent`（bridge） |
| 5 | 工具 permission | `ctx.ask(tool, …)` |
| 6 | 附件注入 | `attachment_refs_json` / `sleuth_llm_json`（bridge） |
| 7 | 文件回传 | `files[].content_base64` → 邮箱 SM4（bridge harvest） |
| 8 | KB sources | `sources[]` harvest → 答复脚注 |
| 9 | HITL | MCP `need_input` → 基座 `question`（Session 内，不在 MCP 阻塞） |

**禁止**：在编排层或 HTTP handler 里直接 `McpManager.call_tool(...)` 绕过 Session。

---

## 7. 相关代码

| 文件 | 说明 |
|------|------|
| [`sleuth/orchestration.py`](../sleuth/orchestration.py) | 模式解析、invoke / parallel / async |
| [`sleuth/config.py`](../sleuth/config.py) | `OrchestrationConfig` + `AgentConfig` 字段 |
| [`sleuth/session.py`](../sleuth/session.py) | `execute_guarded_tool` |
| [`sleuth/mcp/bridge.py`](../sleuth/mcp/bridge.py) | 注入与 harvest |
| [`sleuth/tools/task.py`](../sleuth/tools/task.py) | Mode B 委托 + ACL + delegatable |
| [`docs/AGENT_SCENARIOS.md`](AGENT_SCENARIOS.md) | 场景流程 |
| [`docs/MCP_INTEGRATION.md`](MCP_INTEGRATION.md) | Card Schema |
