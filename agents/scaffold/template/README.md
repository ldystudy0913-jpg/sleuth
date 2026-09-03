# __AGENT_NAME__（Sleuth MCP Agent）

独立项目包：MCP 工具面 + Agent Card + Skill。不修改 sleuth 内核。

完整业务对照：`agents/dd_check`、`agents/dd_reply`。本包只保留 hello `ping`。

## 开发你要改的文件

| 文件 | 做什么 |
|------|--------|
| [`pipeline.py`](__PKG_NAME__/pipeline.py) | **业务逻辑**（TODO） |
| [`mcp_server.py`](__PKG_NAME__/mcp_server.py) | 注册 MCP 工具，只做参数拼装 |
| [`agent.md`](agent.md) | 人设、权限（合格名 `{server}_{tool}`）；`catalog_skills` 点名 COS SOP |
| `skills/*/SKILL.md` | 私有 SOP（有正文才嵌入 Card） |
| [`.env`](.env.example) | `{PKG}_*`：附件 / KB / COS / MCP token。改完重启 MCP |

## 本地启动

```powershell
cd <this-package>
py -3.12 -m pip install -e ".[mcp]"
Copy-Item .env.example .env
py -3.12 -m __PKG_NAME__.mcp_server
```

探活：`GET http://127.0.0.1:__MCP_PORT__/health`

接到 Sleuth：见 [HOWTO_SLEUTH.md](HOWTO_SLEUTH.md)。

文件解析在 Sleuth 完成。三个可选模块始终生成，**只有本包 `.env` 配齐才注册对应工具**（空配置不会挂一个返回空 JSON 的空工具）：

| 模块 | 开关 | 行为 |
|------|------|------|
| `attachments.py` | `__ENV_PREFIX___ATTACHMENTS=1` | `ping` 声明 `attachment_refs_json`；优先 excerpt |
| `kb.py` | 四项 `__ENV_PREFIX___KB_*` | 注册 `kb_search`，返回 `sources[]` |
| `output.py` | 本包 COS 配齐 | 注册 `emit_file`，返回 `files[]`；上传需 `.[cos]`（boto3） |
