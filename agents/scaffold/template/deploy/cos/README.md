# COS / S3 共享 Skill

Sleuth 发现逻辑见仓库 [`sleuth/skill/__init__.py`](../../../../../sleuth/skill/__init__.py)。对象键与 Card 里的 `name` 必须一致。

## 推荐对象布局

```text
s3://<bucket>/sleuth/skills/__COS_SKILL__/SKILL.md
```

也支持：

| 方式 | Sleuth 配置 | 说明 |
|------|-------------|------|
| 前缀扫描 | `SLEUTH_SKILLS_S3=s3://<bucket>/sleuth/skills/` | URI 以 `/` 结尾 |
| 单文件 | `s3://<bucket>/sleuth/skills/__COS_SKILL__/SKILL.md` | 指向 `SKILL.md` 或 zip |
| manifest | `s3://<bucket>/sleuth/skills/manifest.json` | `{ "keys": ["sleuth/skills/__COS_SKILL__/SKILL.md"] }` |

上传本包 `skills_cos/__COS_SKILL__/SKILL.md` 到上表路径。凭证走 `AWS_*` / `SLEUTH_S3_ENDPOINT`（与会话 COS 相同即可）。

## Agent Card

`--skill cos` 或 `both` 时，`get_agent_card` 的 `skills[]` **只写 name**，不嵌 `content`。本地已有同名 skill 时 COS/路径优先，Card 内嵌会被跳过。

专用 Agent 点名后自动注入 SOP，不走 pin。只有还要让默认 agent（`build`）选择器看到时，才需要 `deploy/grant.example.json` 里那条 `resource_kind=skill`。

## Sleuth .env 片段

```env
SLEUTH_SKILLS_S3=s3://<bucket>/sleuth/skills/
SLEUTH_MCP_SERVERS={"__SERVER_NAME__":{"type":"remote","url":"http://127.0.0.1:__MCP_PORT__/mcp","agent":true}}
```
