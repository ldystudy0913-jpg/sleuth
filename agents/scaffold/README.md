# Sleuth Agent 脚手架

给要对接到 Sleuth 的业务方：复制模板，或用生成器产出独立项目包。不要从 `dd_check` / `dd_reply` 里抠样板。完整业务仍看那两个包。

推荐路径：**独立进程 MCP + `agent:true`**。规范见 [`docs/MCP_INTEGRATION.md`](../../docs/MCP_INTEGRATION.md)、[`docs/SKILL_INTEGRATION.md`](../../docs/SKILL_INTEGRATION.md)。

## 选型

| 你要做的 | 怎么做 |
|----------|--------|
| 专用 Agent + 私有 SOP | 默认生成 `skills/<slug>/SKILL.md`，Card 嵌入 `content`，不必 skill grant |
| 复用 COS 上已有 SOP | 在 `agent.md` 写 `catalog_skills:` **只写 name**（不要空 `SKILL.md`）；Sleuth 配 `SLEUTH_SKILLS_S3` |
| 私有一份 + COS 复用一份 | 本地目录有正文；`catalog_skills` 里写其它 name |
| 只要工具、不要人格 | `--tools-only` → snippet `agent:false` |

副作用用 MCP Tool；流程说明用 Skill。密钥只放本包 `.env`（`{PKG}_*`），不要写进 Sleuth `.env`，也不要回退读 `SLEUTH_*`。COS skill 由 **Sleuth 进程** 加载；Agent 包不拉 skill。缺目录 name 时跳过注入，不崩。本地 `skills/` 同名有正文时覆盖 COS/路径同名条目。

## 生成（推荐）

在仓库根目录：

```powershell
py -3.12 agents/scaffold/generate.py --name demo_ops --port 8799
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--name` | （必填） | Agent id，`--agent` / HTTP `agent` |
| `--server` | 去掉下划线的 name | MCP 配置键，合格名前缀 |
| `--port` | `8799` | Streamable HTTP 端口，路径 `/mcp` |
| `--tools-only` | 关 | snippet 使用 `agent:false` |
| `--out` | `agents/<name>` | 输出目录 |
| `--title` | 由 name 生成 | Agent Card / 前端展示名 |
| `--force` | 关 | 覆盖已存在的 `--out` |

然后：

```powershell
cd agents\demo_ops
py -3.12 -m pip install -e ".[mcp]"
py -3.12 -m demo_ops.mcp_server
```

Sleuth `.env` 粘贴生成包里的 `deploy/sleuth.env.snippet`。客户端鉴权可设 `SLEUTH_MCP_HEADERS`，与本包 `{PKG}_MCP_TOKEN` 同一 Bearer。

## 复制模板

把 [`template/`](template/) 拷走后，把 `__AGENT_NAME__`、`__PKG_NAME__`、`__SERVER_NAME__`、`__MCP_PORT__`、`__SKILL_SLUG__`、`__ENV_PREFIX__`、`__AGENT_TRUE__` 等占位符全部替换，并把目录 `__PKG_NAME__` / `__SKILL_SLUG__` 改成真实名字。漏替换时用生成器更安全。

## 模板里要自己实现的脚本

1. `__PKG_NAME__/pipeline.py` — 业务（把 `ping` 换成真实流程）
2. `__PKG_NAME__/mcp_server.py` — 为每个业务函数加 `@server.tool`
3. `agent.md` — 人设；`permission` 必须用合格名 `{server}_{tool}`
4. `skills/*/SKILL.md` — 私有 SOP（正文非空才嵌入）；COS 复用只在 `catalog_skills` 点名

契约已写好、可跑通：`get_agent_card`、`health`（含 `GET /health`）、`ping`。文件解析在 Sleuth 完成；默认 `ping` **不**声明 `attachment_refs_json`（设 `{PKG}_ATTACHMENTS=1` 后重启才声明）。出参 `sources[]` / `files[]` 是可选约定，基座不解析你们的内部逻辑。

## 可选能力（始终生成模块，按 env 注册）

三个模块每次都会拷进包内。空 `.env` **不**注册会返回空 JSON 的空工具；配齐后重启 MCP 即可，不必重新 generate。

| 能力 | `{PKG}_*` | 未配齐 |
|------|-----------|--------|
| 会话摘录 | `ATTACHMENTS=1` | `ping` 无 `attachment_refs_json` |
| 知识库 | `KB_API_URL` + `KB_LOGIN_URL` + `KB_OPENID` + `KB_SERVICEID` | 不注册 `kb_search` |
| 回传文件 | `AWS_ACCESS_KEY_ID`/`COS_SECRET_ID` + secret + `COS_BUCKET` + region 或 endpoint | 不注册 `emit_file` |
| HTTP 鉴权 | `MCP_TOKEN` 非空 | 不装中间件；`GET /health` 始终开放 |

Card 默认仍写 `kb_lookup: deny`。打开本包 KB 时加 `{server}_kb_search: allow`。打开 COS 输出时加 `{server}_emit_file: allow`，`save_output_file` 默认 deny。想改用基座检索：删掉 `kb_lookup` deny 即可。回传文件用 MCP `files[]` **或** Sleuth `save_output_file`，不要把字节/data-URL 写进答复。
