---
name: dd-check-sop
description: >
  用户提交已填写的尽调报告并要求检查、询问可检查场景、查看或删除检查历史时使用。
  通过 MCP 工具 ddcheck_check_report / list_scenarios / list_checks / delete_check 执行。
mcp:
  - ddcheck
tools:
  - ddcheck_check_report
  - ddcheck_list_scenarios
  - ddcheck_list_checks
  - ddcheck_delete_check
  - ddcheck_health
---

# dd-check-sop（尽调报告填写检查）

本 Skill **只提供流程规范**，副作用由 MCP 执行。正文由 Agent Card `skills[].content` 嵌入，跟本 Agent 走，不必单独授 skill grant。

## 前置

已启动本包 MCP，Sleuth 配置例如：

```env
SLEUTH_MCP_SERVERS={"ddcheck":{"type":"remote","url":"http://127.0.0.1:8791/mcp","agent":true}}
```

工具（Sleuth 合格名）：

- `ddcheck_health` — 探活（含 llm / kb / output / mysql 是否已配置）
- `ddcheck_list_scenarios` — 列出可检查场景（读配置，不查库）
- `ddcheck_check_report` — 检查报告；入参 `report_text`、`report_json`、`question`、`report_id`、`scenario`、`proceed_with_gaps`；若本包打开了附件，基座会注入 `attachment_refs_json`
- `ddcheck_list_checks` — 按 `report_id` 列出历史检查元数据（无 Word 字节）
- `ddcheck_delete_check` — 按 `check_id` 删除一次历史（库行 + COS，不动会话附件）

## 能检查什么

用户问「能检查哪些场景 / 有哪些报告」时：**只调用** `ddcheck_list_scenarios`，用中文转述 `title` / `description` / `aliases`。不要编造，不要调用 `check_report`。

## 做一次检查

1. （可选）调用 `ddcheck_health`。若 `llm` 为 false，本包 `.env` 未配齐 `DD_CHECK_LLM_*`；从 Sleuth 调用时仍可能用会话模型完成检查。两头都没有时工具 `ok` 为 false，不要编造结论。
2. 将用户提供的正文放入 `report_text`；结构化数据放入 `report_json`；业务报告编号放入 `report_id`。用户问题放入 `question`。不要把附件密文传给工具。
3. 场景 `scenario`：能从话术映射则填 id 或别名（`开户|新开|对公开户` → `corp_onboarding`；`变更|信息变更|对公变更` → `corp_change`）。映射不到则先空着。
4. 调用 `ddcheck_check_report`。**正在做检查时**，pipeline 模式默认只调一次主工具，不要拆成多步检索。
   - 若返回 `status=need_input`：**不要继续检查、不要编造结论**。向用户列出 `missing`。场景缺项只反问 **一次**，选项建议：各场景标题 + 「不确定，按默认检查」。
   - 用户选定场景：带上 `scenario` 再调用（不要设 `proceed_with_gaps`）。
   - 用户说不清楚 / 默认 / 继续，或回答仍对不上别名：再次调用并设 `proceed_with_gaps=true`（空 scenario 走默认检查，不再问场景）。
   - 用户补了 `report_id` 或材料：带上更新后的入参再调用。
5. 用中文归纳：
   - 默认场景：总分 `score`、每条 `findings[]`（问题 + `location`）
   - 开户/变更：`summary`、`conflicts[]`（事项 + 判断）
   - 若有 `files[]`：说明 Word 已作为助手文件回传，可下载（仅当次这一份）
   - 若有 `sources[]`：保留 URL，不要改写
6. 工具 `ok` 为 false 时说明 `detail`，不要编造 findings/conflicts。

## 历史

用户问某份报告过去查过几次：`ddcheck_list_checks`。只归纳时间、场景、文件名、`check_id`。下载历史 Word 由前端打本包 `GET /v1/checks`，不要把 base64 念给用户。
