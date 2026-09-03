---
name: __SKILL_SLUG__
description: >
  本 Agent 的私有 SOP。用户要求演示连通性、回显消息或检查 MCP 是否可用时使用。
  通过 MCP 工具 __SERVER_NAME___ping 执行。
mcp:
  - __SERVER_NAME__
tools:
  - __SERVER_NAME___ping
  - __SERVER_NAME___health
---

# __SKILL_SLUG__（私有 SOP）

本 Skill **只提供流程规范**，副作用由 MCP 执行。正文由 Agent Card `skills[].content` 嵌入，跟本 Agent 走，不必单独授 skill grant。

## 前置

已启动本包 MCP，Sleuth 配置例如：

```env
SLEUTH_MCP_SERVERS={"__SERVER_NAME__":{"type":"remote","url":"http://127.0.0.1:__MCP_PORT__/mcp","agent":true}}
```

工具（Sleuth 合格名）：

- `__SERVER_NAME___health` — 探活
- `__SERVER_NAME___ping` — 回显；若本包 `.env` 打开了附件，可带 `attachment_refs_json`（会话邮箱摘录，由基座注入）

## 流程

1. （可选）调用 `__SERVER_NAME___health`。
2. 将用户要回显的文本传入 `__SERVER_NAME___ping` 的 `message`。
3. 用中文归纳 `echo`；不要把完整附件明文读给无关上下文。
4. 若返回顶层 `sources[]`，保留 URL，不要改写。Sleuth 会在最终答复末尾列出。
