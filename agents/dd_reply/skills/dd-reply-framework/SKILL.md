---
name: dd-reply-framework
description: >
  对公开户尽调答复框架生成。当用户提供风险点编码（如 C001）或风险点名称、
  及 KYC 系统字段、或要求生成答复框架/待核实清单时使用。通过 MCP 工具
  ddreply_generate_reply_framework 执行；知识库按编码或名称检索；禁用词来自 lexicon。
---

# 尽调答复框架生成（规范）

## 前置

已配置并启动 `dd_reply` 工具面（MCP），例如：

```env
SLEUTH_MCP_SERVERS={"ddreply":{"type":"remote","url":"http://127.0.0.1:8792/mcp","agent":true}}
```

工具（Sleuth 限定名）：

- `ddreply_health` — 探活（含知识库 / LLM 配置状态）
- `ddreply_list_risk_codes` — 列出知识库支持的风险点编码
- `ddreply_lookup_risk_kb` — 按编码或名称检索知识库
- `ddreply_list_lexicon` — 查看禁用词知识库
- `ddreply_generate_reply_framework` — 生成四段式框架

本 Skill **只提供流程规范**，生成由 MCP/流水线执行。

## 流程

1. （可选）`ddreply_health`。
2. 收集入参：
   - `risk_codes_json`：编码数组，如 `["C001","C003"]`（可空）
   - `risk_names_json`：名称数组，如 `["行政处罚记录"]`（可空；与编码至少填一类）
   - 也可把名称直接放进 `risk_codes_json`，将作为检索 question
   - 10 个 KYC 字段字符串（客户名称、成立时间、经营范围、员工人数、注册资本、年销售收入、受益所有人身份信息、主营业务、开户主要目的、账户交易模式预估）
   - 可选 `attachment_refs_json`（会话邮箱 HTTPS 引用，由 Sleuth 注入；生产路径）
   - 可选 `local_paths_json`（仅本机测试）或 `invest_id`（业务 COS）
3. 调用 `ddreply_generate_reply_framework`。
   - 若返回 `status=need_input`：**不要继续生成**。向用户列出 `missing`（当前分析还缺这些字段），并询问是否还有其他缺失信息要补充。用内置 `question` 工具，选项建议：
     - 「补充信息」（Recommended）
     - 「没有补充，继续分析」
   - 用户提供了新字段：带上更新后的入参再调用（不要设 `proceed_with_gaps`）。
   - 用户明确说没有补充 / 继续：再次调用并设 `proceed_with_gaps=true`。此时空字段仍会在预分析标明「本步无法判断」，不得臆造。
4. 向用户展示返回的 `markdown`（或四段字段）；保留全部【待核实N】槽位，不要替客户经理填最终结论。第 4 段须是「核实结果 → 可排除 / 可缓释 / 无法排除」对照，免责声明不能替代该段。文末虚线后的灰色「知识来源」是核对清单，不要并进答复正文。
5. 若 `meta.missing_codes` 非空，提示知识库未覆盖，需人工补知识或补问。
6. 若 `meta.soft_warnings` / `blocked_phrases` 有值，提醒注意表述合规。

## 注意

- 助手定位是辅助工具：**最终判定由人工作出**（对外可简要说明；勿逐条宣读内部禁用词/实现约束）。
- 生成与展示框架时遵守合规表述；具体禁用词以知识库为准，由工具侧校验，不必在对话里背诵规则。
- 勿复述完整证件号等敏感信息；可提示脱敏。
- 一个请求可含一个或多个风险点编码和/或名称。
- 用户仅询问身份/能力时：用简短产品口径回答，不要展开「我不会伪造…」「我不会输出…」等约束清单。
