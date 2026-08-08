# dd_analyst — 尽调报告检查 Agent

独立 Agent 包（**不修改 sleuth 源码**）。交付物是 Agent，不是独立业务应用。

## 包结构

```text
agents/dd_analyst/
  agent.md                 # Sleuth Agent 人设 + 权限
  skills/                  # 检查 SOP（Skill，不执行）
  dd_check/                # 检查能力（LangGraph + 规则/附件）
  tests/
  README.md                # 本文件
```

## 配置使用（推荐：MCP 注册 Agent）

### 1. 安装并启动本 Agent 的工具面

```powershell
cd C:\Users\15385\myproject\sleuth\agents\dd_analyst
py -3.12 -m pip install -e ".[mcp]"
py -3.12 -m dd_check.mcp_server
# 默认 http://127.0.0.1:8791/mcp
```

### 2. Sleuth 只配 MCP（`agent:true` 自动拉人设 + Skill）

```env
SLEUTH_MCP_SERVERS={"ddcheck":{"type":"remote","url":"http://127.0.0.1:8791/mcp","agent":true}}
```

无需拷贝 `agent.md`，也无需 `SLEUTH_SKILLS_PATHS`（Skill 在 Agent Card 内）。

**兼容旧配置**：去掉 `"agent":true` 后仍只挂工具；可继续本地 `.opencode/agent` + `SLEUTH_SKILLS_PATHS`。

### 3. 启动对话

```powershell
cd C:\Users\15385\myproject\sleuth
py -3.12 -m sleuth --agent dd_analyst --yolo
```

示例提问：

```text
请检查下面这份尽调报告（phase=CHECK）：
{ ... 完整业务 JSON ... }
```

模型应：按 Skill → 调 `ddcheck_run_dd_check` → 中文归纳 score/findings。

### HITL（人工确认，可选）

默认关闭。开启时**必须**配置持久 checkpoint（运维先建表，代码不 CREATE）：

```powershell
# 建表（一次性）
sqlite3 .\dd_check_checkpoints.sqlite3 < .\deploy\ddl_langgraph_checkpoint.sql

$env:DD_CHECK_CHECKPOINT_SQLITE_PATH="C:\Users\15385\myproject\sleuth\agents\dd_analyst\dd_check_checkpoints.sqlite3"
$env:DD_CHECK_HITL="1"
# 可选：仅有 FAIL 时才暂停
# $env:DD_CHECK_HITL_ON_FAIL_ONLY="1"
py -3.12 -m dd_check.mcp_server
```

- `run_dd_check` 可能返回 `status=awaiting_human` + `thread_id` → 对话确认后调 `ddcheck_resume_dd_check`（**续跑**，非回滚）。
- 要回到更早节点：`ddcheck_list_dd_checkpoints` → `ddcheck_rollback_dd_check`（时间旅行分叉；**不会**回滚 Sleuth 聊天记录）。
- 批量检查始终跳过 HITL。
- 仅配置 `DD_CHECK_CHECKPOINT_SQLITE_PATH`（HITL 关）时，同步检查也会落节点 checkpoint 并返回 `thread_id`，便于 list/rollback。

DDL 文件：`deploy/ddl_langgraph_checkpoint.sql`（与结果表 `ddl_dd_check_result.sql`、附件元数据 `ddl_ddp_file.sql` 分开）。

## 输入 / 输出

**输入（业务 JSON）**：`reportId`、`investId`、`result`、`question`、`busCode`、`busCodeDesc`、`currentDateTime`、`custType`、`approveData`、`phase`、`bankId`。

**输出**：`score`、`grade`、`summary`、`findings[]`、`strategy_id`、`enabled_dimensions`、`skipped_attachments`、可选 `trace`。

## 自测（不经过 Sleuth）

```powershell
cd agents\dd_analyst
py -3.12 -m unittest discover -s tests -v
```

## 可选 REST 联调

```powershell
py -3.12 -m dd_check.api   # :8790，与检查图同一套 runner
```

## 迁出仓库

整目录 `agents/dd_analyst` 可拷到任意位置；Sleuth 只需 Agent md、Skill 路径与 MCP URL。
