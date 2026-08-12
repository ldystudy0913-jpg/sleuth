---
name: dd-report-check
description: >
  银行尽调报告智能检查。当用户提供尽调报告 JSON（含 reportId、result、custType、phase 等）
  或要求检查/回检尽职调查报告时使用。通过 MCP 工具 ddcheck_run_dd_check 执行；
  若返回 awaiting_human 则询问用户后 ddcheck_resume_dd_check；
  若需回到更早节点重跑则 list_dd_checkpoints + rollback_dd_check。
---

# 尽调报告检查（规范）

## 前置

已配置并启动 `dd_analyst` 工具面（MCP），例如：

```env
SLEUTH_MCP_SERVERS={"ddcheck":{"type":"remote","url":"http://127.0.0.1:8791/mcp"}}
```

工具（Sleuth 限定名）：

- `ddcheck_health` — 探活（含 `hitl_enabled` / `checkpoint_sqlite_configured`）
- `ddcheck_run_dd_check` — 启动检查
- `ddcheck_resume_dd_check` — HITL **续跑**（非回滚）
- `ddcheck_list_dd_checkpoints` — 列出某 `thread_id` 的节点 checkpoint
- `ddcheck_rollback_dd_check` — 从更早 `checkpoint_id` **时间旅行分叉**再跑（不回滚 Sleuth 聊天）
- `ddcheck_run_dd_batch` — 批量（始终同步，跳过 HITL）
- `ddcheck_describe_graph` — 图说明

本 Skill **只提供流程规范**，检查由 MCP/图执行。

## 流程

1. （可选）`ddcheck_health`。
2. 将用户 JSON 字段传入 `ddcheck_run_dd_check`（**`result` 必须是表单 sections 的 JSON 字符串，原样传递，不要改写/抽稀字段；不要把整包 CheckRequest 再塞进 `result`**）。
3. 看返回 `status`：
   - `completed` / 带 `score` 的最终结果 → 中文归纳。
   - `awaiting_human` → 展示 `interrupt.findings_preview` / score / grade，请用户选择：
     - 通过 → `decision_json={"action":"approve"}`
     - 改摘要 → `{"action":"edit_summary","summary":"..."}`
     - 驳回 → `{"action":"reject","feedback":"..."}`
     然后 `ddcheck_resume_dd_check(thread_id=..., decision_json=...)`。
4. 用户要「回到某步重跑」：`list_dd_checkpoints` → 选 `checkpoint_id` → `rollback_dd_check`。
5. 不要伪造检查结果；MCP 失败如实说明。

## 字段校验语义

- 引擎只校验**报告里已出现的字段的值**。
- **字段未出现**（该场景表单不含此项）≠ 缺陷，不报「缺少必填」。
- **字段出现但值为空** → 才记空值 WARN/FAIL。
- 不同尽调场景字段集合不同时，以当次 `result` 实际 section 为准。

## 注意

- HITL 由服务侧 `DD_CHECK_HITL` 控制；开启时必须配置 `DD_CHECK_CHECKPOINT_SQLITE_PATH`（运维先建表）。
- `resume` ≠ `rollback`；Sleuth 会话不会因 rollback 倒带。
- 批量检查不走人工业确认。
- 勿复述完整证件号/附件明文。
