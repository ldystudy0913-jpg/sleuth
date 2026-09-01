# __AGENT_NAME__（Sleuth MCP Agent）

独立项目包：MCP 工具面 + Agent Card + Skill。不修改 sleuth 内核。

完整业务对照：`agents/dd_analyst`、`agents/dd_reply`。本包只保留 hello `ping`。

## 开发你要改的文件

| 文件 | 做什么 |
|------|--------|
| [`pipeline.py`](__PKG_NAME__/pipeline.py) | **业务逻辑**（TODO） |
| [`mcp_server.py`](__PKG_NAME__/mcp_server.py) | 注册 MCP 工具，只做参数拼装 |
| [`agent.md`](agent.md) | 人设、权限（合格名 `{server}_{tool}`） |
| `skills/` 或 `skills_cos/` | SOP；私有嵌入 Card，共享只点名 |

## 本地启动

```powershell
cd <this-package>
py -3.12 -m pip install -e ".[mcp]"
Copy-Item .env.example .env
py -3.12 -m __PKG_NAME__.mcp_server
```

探活：`GET http://127.0.0.1:__MCP_PORT__/health`

接到 Sleuth：见 [HOWTO_SLEUTH.md](HOWTO_SLEUTH.md)。当前 Skill 模式：`__SKILL_MODE__`。
