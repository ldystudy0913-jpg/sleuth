# 将 dd_check 挂到 Sleuth

本包始终生成本地 SOP（`skills/dd-check-sop/SKILL.md`，嵌入 Agent Card）以及 attachments / kb / output / llm / hitl 模块。工具是否对外暴露由**本包 `.env`** 决定，改完重启 MCP，不必重新 generate。

## 1. 启动工具面

```powershell
cd <this-package>
py -3.12 -m pip install -e ".[mcp,cos,docx]"
# 填写本包 .env：DD_CHECK_LLM_* 配齐则用本包模型；留空则用 Sleuth 会话模型。KB 按需
Copy-Item .env.example .env
py -3.12 -m dd_check.mcp_server
```

默认 `http://127.0.0.1:8791/mcp`。探活：`GET http://127.0.0.1:8791/health`（即使配了 MCP token 也始终开放）。

## 2. 配置 Sleuth

把 [`deploy/sleuth.env.snippet`](deploy/sleuth.env.snippet) 粘进 Sleuth 工作目录 `.env`。

`agent:true` 会调用 `get_agent_card`，注册人格 `dd_check`。`--tools-only` 生成时 snippet 为 `agent:false`（工具对所有会话可见，不注册专用人格）。

- **私有 SOP**：Card 带 `skills[].content`，跟 Agent 走，不必单独 skill grant。
- **复用 COS 上已有 SOP**：在 `agent.md` 写 `catalog_skills:` 只填 name（不要建空 `SKILL.md`）。Sleuth 进程用 `SLEUTH_SKILLS_S3` 拉包；本 MCP **不**拉 skill。缺目录时跳过注入，不崩。
- 本地 `skills/` 同名且有正文时，覆盖 COS/路径同名条目。

客户端与本包共用一个 Bearer 时：Sleuth 设 `SLEUTH_MCP_HEADERS={"Authorization":"Bearer <token>"}`（或写在该 server 的 `headers`），本包设 `DD_CHECK_MCP_TOKEN=<token>`。

## 3. 岗位授权（若 `SLEUTH_ACL_ENABLED=1`）

`PUT /v1/directory/grants` 使用 [`deploy/grant.example.json`](deploy/grant.example.json)。

- 专用 Agent：至少一条 `resource_kind=agent`、`resource_id=dd_check`。
- COS 共享 skill 还要给 **build** 选择器看见时，再加 `resource_kind=skill`、`resource_id=<catalog-skill-name>`。私有 Card SOP 不需要 skill grant。

## 4. 运行

```powershell
py -3.12 -m sleuth --agent dd_check
```

HTTP：`POST /v1/sessions` body `{ "agent": "dd_check" }`。

示例话术：请检查这份尽调报告填写是否有问题，并给出评分和 Word。

## 5. 会话文件与可选 JSON 约定

文件解析统一在 Sleuth：上传进会话邮箱后，基座解密并抽出 excerpt（PDF/xlsx/docx/图片视觉）。本 MCP 进程默认拿不到密文，也不该自己解 SM4。Sleuth 不解析你们的 markdown / LangGraph；工具返回值是字符串。只有希望基座帮你做 UI/邮箱时，才用可选顶层 JSON：

| 约定 | 谁用 | 不遵守会怎样 |
|------|------|----------------|
| 入参 `attachment_refs_json` | 要读会话附件 | 收不到摘录 |
| 入参 `sleuth_llm_json` | 本包 LLM 未配齐时用会话模型 | 直连 MCP 且本包 LLM 为空则检查失败 |
| 出参 `sources[]`（`title` + `http(s) url`） | 答复末尾灰色「知识来源」 | 不附来源段 |
| 出参 `files[]`（`content_base64` 或已有 `https url` / `object_key`） | Sleuth 加密写入会话邮箱，进 `done.files` | 前端收不到回传文件 |

禁止 data-URL / file-URL。

本包能力默认**生成代码、按 env 注册**（空 env 不注册空工具）：

| 能力 | 本包 `.env` | 未配齐时 |
|------|-------------|---------|
| 会话摘录 | `DD_CHECK_ATTACHMENTS=1` | `check_report` 不声明 `attachment_refs_json` |
| 人工介入 | `DD_CHECK_HITL=1` | 空材料 / 空 report_id / 未映射场景不返回 `need_input`，直接默认检查 |
| 内部 LLM | `DD_CHECK_LLM_BASE_URL` + `_API_KEY` + `_MODEL` | 用 Sleuth 注入的会话模型；两头都空则检查失败 |
| 知识库 | `DD_CHECK_KB_API_URL` + `_LOGIN_URL` + `_OPENID` + `_SERVICEID` | 不注册 `kb_search` |
| 回传文件 MCP 工具 | COS：access + secret + bucket + (region 或 endpoint) | 不注册 `emit_file`；Word 仍走 `files[].content_base64` |
| HTTP 鉴权 | `DD_CHECK_MCP_TOKEN` 非空 | 不装中间件 |

历史检查（本包 MySQL + COS，代码不建表）：

| 能力 | 本包 `.env` | 未配齐时 |
|------|-------------|---------|
| 检查历史落库 | `DD_CHECK_MYSQL_*` 或 `SLEUTH_LOG_TRACE=1`（官方 MySql） | 检查仍回传当次 Word，不写历史 |
| 历史 COS | 与 `emit_file` 相同的 COS 四项 | 不上传历史对象 |
| 每报告保留次数 | `DD_CHECK_HISTORY_MAX`（默认 10） | 超限 FIFO 删最旧行和对象 |

请先在库中执行仓库 [`docs/ddl_dd_check_mysql.sql`](../../docs/ddl_dd_check_mysql.sql)。

场景：`config/scenarios/`。用户问「能检查什么」走 MCP `list_scenarios`（读磁盘）。未指定场景时 HITL 反问一次，仍不清则 `default`（现有 `config/prompts`）。

历史 HTTP（鉴权 `DD_CHECK_MCP_TOKEN`，信封与基座错误码相同；`/health` 除外）：

- `GET /v1/checks/scenarios`
- `GET /v1/checks?report_id=`
- `GET /v1/checks/{check_id}/file`
- `DELETE /v1/checks/{check_id}`

开了 `SLEUTH_LOG_TRACE=1` 时业务失败也是 HTTP 200 + `{code, msg, content}`。

## 6. 无 Card 回退

省略 `agent:true` 时只挂工具。可继续用本地 `agent.md` + `SLEUTH_SKILLS_PATHS=<this-package>/skills`。
