---
name: dd-check-sop
description: >
  用户提交已填写的尽调报告（正文、JSON、纯文本或附件）并要求填写检查、逻辑核对、
  附件核验或出具检查评分/Word 时使用。通过 MCP 工具 ddcheck_check_report 执行。
mcp:
  - ddcheck
tools:
  - ddcheck_check_report
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

- `ddcheck_health` — 探活（含 llm / kb / output 是否已配置）
- `ddcheck_check_report` — 检查报告；入参 `report_text`、`report_json`、`question`、`proceed_with_gaps`；若本包打开了附件，基座会注入 `attachment_refs_json`

## 流程

1. （可选）调用 `ddcheck_health`。若 `llm` 为 false，本包 `.env` 未配齐 `DD_CHECK_LLM_*`；从 Sleuth 调用时仍可能用会话模型完成检查。两头都没有时工具 `ok` 为 false，不要编造分数。
2. 将用户提供的正文放入 `report_text`；结构化数据放入 `report_json`（保持原样 JSON 字符串）。用户问题放入 `question`。不要把附件密文传给工具。
3. 调用 `ddcheck_check_report`，等待返回。**pipeline 模式（Card `orchestration: pipeline`）下默认只调一次主工具**，不要拆成多步 MCP 调用。
   - 若返回 `status=need_input`：**不要继续检查、不要编造分数**。向用户列出 `missing`（当前还缺这些材料），并询问是否还有其他缺失信息要补充。用内置 `question` 工具，选项建议：
     - 「补充信息」（Recommended）
     - 「没有补充，继续检查」
   - 用户提供了新材料：带上更新后的入参再调用（不要设 `proceed_with_gaps`）。
   - 用户明确说没有补充 / 继续：再次调用并设 `proceed_with_gaps=true`。
4. 用中文归纳：
   - 总分 `score`（配置的分制，可有一位小数）
   - 每条 `findings[]`：问题 + 对应 `location`
   - 若有 `files[]`：说明 Word 已作为助手文件回传，可下载
   - 若有 `sources[]`：保留 URL，不要改写
5. 工具 `ok` 为 false 时说明 `detail`，不要编造 findings。
