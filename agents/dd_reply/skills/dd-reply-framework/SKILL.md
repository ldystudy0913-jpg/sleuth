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
   - 10 字段字符串（缺失也要传空串，由预分析标明「本步无法判断」）
   - 可选 `local_paths_json`（测试本地附件）或 `invest_id`（生产 COS）
3. 调用 `ddreply_generate_reply_framework`。
4. 向用户展示返回的 `markdown`（或四段字段）；保留全部【待核实N】槽位，不要替客户经理填最终结论。
5. 若 `meta.missing_codes` 非空，提示知识库未覆盖，需人工补知识或补问。
6. 若 `meta.soft_warnings` / `blocked_phrases` 有值，提醒注意表述合规。

## 注意

- 助手定位是辅助工具：**最终判定由人工作出**（对外可简要说明；勿逐条宣读内部禁用词/实现约束）。
- 生成与展示框架时遵守合规表述；具体禁用词以知识库为准，由工具侧校验，不必在对话里背诵规则。
- 勿复述完整证件号等敏感信息；可提示脱敏。
- 一个请求可含一个或多个风险点编码和/或名称。
- 用户仅询问身份/能力时：用简短产品口径回答，不要展开「我不会伪造…」「我不会输出…」等约束清单。
