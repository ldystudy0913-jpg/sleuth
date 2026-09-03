# COS / S3 共享 Skill

Sleuth 发现逻辑见仓库 [`sleuth/skill/__init__.py`](../../../../../sleuth/skill/__init__.py)。对象键目录名与 Card `catalog_skills` / `skills[].name` 必须一致。本 MCP 进程**不**拉 skill；只有 Sleuth 读 `SLEUTH_SKILLS_S3`。

## 推荐对象布局

```text
s3://<bucket>/sleuth/skills/<catalog-skill-name>/SKILL.md
```

也支持：

| 方式 | Sleuth 配置 | 说明 |
|------|-------------|------|
| 前缀扫描 | `SLEUTH_SKILLS_S3=s3://<bucket>/sleuth/skills/` | URI 以 `/` 结尾 |
| 单文件 | `s3://<bucket>/sleuth/skills/<catalog-skill-name>/SKILL.md` | 指向 `SKILL.md` 或 zip |
| manifest | `s3://<bucket>/sleuth/skills/manifest.json` | `{ "keys": ["sleuth/skills/<catalog-skill-name>/SKILL.md"] }` |

把要共享的 SOP 传到上表路径。凭证走 Sleuth 进程的 `AWS_*` / `SLEUTH_S3_ENDPOINT`（与会话 COS 相同即可）。

## Agent Card

在 `agent.md` 写 `catalog_skills:` **只写 name**，不要在本包建空的 `SKILL.md`。Sleuth 按目录查找；找不到该 name 时跳过注入，不崩。

本包 `skills/` 里已有同名且正文非空时，Card 嵌入本地 SOP，并覆盖目录里同名 COS/路径条目。

专用 Agent 点名后自动注入 SOP，不走 pin。只有还要让默认 agent（`build`）选择器看到时，才需要 `deploy/grant.example.json` 里那条 `resource_kind=skill`。

## Sleuth .env 片段

```env
SLEUTH_SKILLS_S3=s3://<bucket>/sleuth/skills/
SLEUTH_MCP_SERVERS={"__SERVER_NAME__":{"type":"remote","url":"http://127.0.0.1:__MCP_PORT__/mcp","agent":true}}
```
