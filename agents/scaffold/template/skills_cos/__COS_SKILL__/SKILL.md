---
name: __COS_SKILL__
description: >
  可上传到 COS / S3 的共享 SOP。Card 只写 name、不嵌 content；Sleuth 从
  SLEUTH_SKILLS_S3 或 SLEUTH_SKILLS_PATHS 加载同名 skill。
mcp:
  - __SERVER_NAME__
tools:
  - __SERVER_NAME___ping
  - __SERVER_NAME___health
---

# __COS_SKILL__（COS 共享 SOP）

把本目录打成 `s3://<bucket>/sleuth/skills/__COS_SKILL__/SKILL.md`（见 `deploy/cos/README.md`）。

专用 Agent 的 Card 点名本 skill 后会自动注入，不走默认 agent 的 pin。若还要让 **build** 选择器看到它，需要 `mem_grant` 的 `resource_kind=skill`。

## 流程

1. （可选）`__SERVER_NAME___health`。
2. `__SERVER_NAME___ping`，传入 `message`。
3. 中文归纳；保留 `sources[]` 里的 URL。
