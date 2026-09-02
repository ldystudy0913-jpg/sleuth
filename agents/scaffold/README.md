# Sleuth Agent 脚手架

给要对接到 Sleuth 的业务方：复制模板，或用生成器产出独立项目包。不要从 `dd_analyst` / `dd_reply` 里抠样板。完整业务仍看那两个包。

推荐路径：**独立进程 MCP + `agent:true`**。规范见 [`docs/MCP_INTEGRATION.md`](../../docs/MCP_INTEGRATION.md)、[`docs/SKILL_INTEGRATION.md`](../../docs/SKILL_INTEGRATION.md)。

## 选型

| 你要做的 | `--skill` | Skill 怎么走 |
|----------|-----------|--------------|
| 专用 Agent + 私有 SOP | `private`（默认） | Card `skills[].content` 嵌入 `SKILL.md`，不必 skill grant |
| 专用 Agent + COS 上已有/要共享的 SOP | `cos` | Card 只写 `name`；上传 `skills_cos/`，配 `SLEUTH_SKILLS_S3` |
| 私有一份 + COS 复用一份 | `both` | 两种都生成 |
| 只要工具、不要人格 | `none` | snippet 里 `agent:false`，工具对所有会话可见 |

副作用用 MCP Tool；流程说明用 Skill。密钥只放本包 `.env`（`__ENV_PREFIX___*`），不要写进 Sleuth `.env`。

## 生成（推荐）

在仓库根目录：

```powershell
py -3.12 agents/scaffold/generate.py --name demo_ops --port 8799 --skill private
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--name` | （必填） | Agent id，`--agent` / HTTP `agent` |
| `--server` | 去掉下划线的 name | MCP 配置键，合格名前缀 |
| `--port` | `8799` | Streamable HTTP 端口，路径 `/mcp` |
| `--skill` | `private` | `private` / `cos` / `both` / `none` |
| `--attachments` | 关 | 会话摘录 helper；`ping` 声明 `attachment_refs_json` |
| `--kb` | 关 | 本包 `kb_search` 桩（`sources[]`）；Card `kb_lookup: deny` |
| `--output` | 关 | `emit_file` 回传桩（`files[]`）；Card `save_output_file: allow` |
| `--out` | `agents/<name>` | 输出目录 |
| `--title` | 由 name 生成 | Agent Card / 前端展示名 |
| `--force` | 关 | 覆盖已存在的 `--out` |

然后：

```powershell
cd agents\demo_ops
py -3.12 -m pip install -e ".[mcp]"
py -3.12 -m demo_ops.mcp_server
```

Sleuth `.env` 粘贴生成包里的 `deploy/sleuth.env.snippet`。

## 复制模板

把 [`template/`](template/) 拷走后，把 `__AGENT_NAME__`、`__PKG_NAME__`、`__SERVER_NAME__`、`__MCP_PORT__`、`__SKILL_MODE__` 等占位符全部替换，并把目录 `__PKG_NAME__` / `__PRIVATE_SKILL__` / `__COS_SKILL__` 改成真实名字。漏替换时用生成器更安全。

## 模板里要自己实现的脚本

1. `__PKG_NAME__/pipeline.py` — 业务（把 `ping` 换成真实流程）
2. `__PKG_NAME__/mcp_server.py` — 为每个业务函数加 `@server.tool`
3. `agent.md` — 人设；`permission` 必须用合格名 `{server}_{tool}`
4. `skills/*/SKILL.md` 或 `skills_cos/*/SKILL.md` — SOP，工具名同样用合格名

契约已写好、可跑通：`get_agent_card`、`health`（含 `GET /health`）、`ping`。文件解析在 Sleuth 完成；默认生成的 `ping` **不**声明 `attachment_refs_json`。出参 `sources[]` / `files[]` 是可选约定，基座不解析你们的内部逻辑。

## 可选能力（默认关）

三个 flag 互不依赖，默认都不生成对应模块：

```powershell
py -3.12 agents/scaffold/generate.py --name demo_ops --attachments --kb --output
```

| Flag | 生成什么 | Card |
|------|----------|------|
| `--attachments` | `attachments.py`：优先 `excerpt`；无 excerpt 且未加密才允许 http(s) 下载；跳过 data:/file: 与密文 | `ping` 带 `attachment_refs_json` |
| `--kb` | `kb.py` + `kb_search`（读 `{PKG}_KB_API_URL`，空则 `sources: []`） | `{server}_kb_search: allow`，`kb_lookup: deny` |
| `--output` | `output.py` + `emit_file`（返回 `files[]`） | `{server}_emit_file: allow`，`save_output_file: allow` |

不加 `--kb` 时仍写 `kb_lookup: deny`（专用人格默认不碰 build 的知识库）。不加 `--output` 时 `save_output_file: deny`。想改用基座检索：删掉 `kb_lookup` deny、去掉本包 `kb_search` 即可。回传文件用 MCP `files[]` **或** Sleuth `save_output_file`，不要把字节/data-URL 写进答复。
