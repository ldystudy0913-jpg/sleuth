# __AGENT_NAME__（Sleuth MCP Agent）

独立项目包：MCP 工具面 + Agent Card + Skill。不修改 sleuth 内核。

完整业务对照：`agents/dd_check`、`agents/dd_reply`。本包只保留 hello `ping`。二次开发步骤见 [HOWTO_SLEUTH.md](HOWTO_SLEUTH.md) 第 0 节（改哪些函数、开了可选能力还要不要改代码）。

## 开发你要改的文件

| 文件 | 做什么 |
|------|--------|
| [`pipeline.py`](__PKG_NAME__/pipeline.py) | **业务逻辑**（TODO） |
| [`mcp_server.py`](__PKG_NAME__/mcp_server.py) | 注册 MCP 工具，只做参数拼装 |
| [`agent.md`](agent.md) | 人设、权限（合格名 `{server}_{tool}`）；`catalog_skills` 点名 COS SOP |
| `skills/*/SKILL.md` | 私有 SOP（有正文才嵌入 Card） |
| [`.env`](.env.example) | `{PKG}_*`：附件 / HITL / KB / LLM / COS / MCP token。改完重启 MCP |

## 本地启动

```powershell
cd <this-package>
py -3.12 -m pip install -e ".[mcp]"
Copy-Item .env.example .env
py -3.12 -m __PKG_NAME__.mcp_server
```

探活：`GET http://127.0.0.1:__MCP_PORT__/health`

接到 Sleuth：见 [HOWTO_SLEUTH.md](HOWTO_SLEUTH.md)。

文件解析在 Sleuth 完成。可选模块始终生成，**只有本包 `.env` 配齐才注册对应工具**（空配置不会挂一个返回空 JSON 的空工具）。HITL 不注册新工具。

「配齐就自动可用」只有 `kb_search` / `emit_file` / MCP token。HITL、LLM、新工具读附件、进度**必须改代码**，见 HOWTO 第 0.3 节。

| 模块 | 开关 | 开了自动有什么 | 你还要改什么 |
|------|------|----------------|--------------|
| `attachments.py` | `__ENV_PREFIX___ATTACHMENTS=1` | 仅演示 `ping` 声明 `attachment_refs_json` | 新工具自己声明该参数，pipeline 用 `summarize_refs` |
| `hitl.py` | `__ENV_PREFIX___HITL=1` | 演示 ping 空 message 返回 `need_input` | 按业务自己列 `missing`；SOP 调 `question` |
| `llm.py` | 可选 `__ENV_PREFIX___LLM_*` | 不会自动调模型 | pipeline 里 `chat_completion` / `complete_json` |
| `kb.py` | 四项 `__ENV_PREFIX___KB_*` | 注册 `kb_search`，返回 `sources[]` | 一般只改 SOP；流水线内检索再调 `search()` |
| `output.py` | 本包 COS 配齐 | 注册 `emit_file` | 或业务 JSON 带 `files[].content_base64`，不必配 COS |
| `progress.py` | 无单独开关 | 模块已拷入 | 长步骤调 `progress_fn("阶段")` |
