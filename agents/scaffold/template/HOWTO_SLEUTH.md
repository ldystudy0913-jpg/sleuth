# 将 __AGENT_NAME__ 挂到 Sleuth

本包始终生成本地 SOP（`skills/__SKILL_SLUG__/SKILL.md`，嵌入 Agent Card）以及 attachments / kb / output / llm / hitl 模块。工具是否对外暴露由**本包 `.env`** 决定，改完重启 MCP，不必重新 generate。

**二次开发先读下面第 0 节。** 只配 `.env` 往往不够：KB / emit_file / MCP token 开了会自动注册；HITL / LLM / 新工具读附件 / 进度必须改你自己的代码。

## 0. 二次开发：改哪些函数、可选能力怎么用

生成出来的包能 ping 通 Sleuth，不等于业务已经接好。对照完整业务看 `agents/dd_check`、`agents/dd_reply`（不要整包拷逻辑）。

### 0.1 文件角色

| 文件 | 生成后你要做什么 | 开了可选能力后？ |
|------|------------------|------------------|
| `__PKG_NAME__/pipeline.py` | **必改。** 把 `ping` 换成真实流程 | 附件 / LLM / 进度都在这里调用 |
| `__PKG_NAME__/mcp_server.py` | **必改。** 每个业务函数一个 `@server.tool` | 自己声明入参、自己算 `missing`（见 `_ping_payload` 注释） |
| `agent.md` | **必改。** 人设 + `permission`（合格名 `{server}_{tool}`） | 打开 KB / emit_file 时补 allow；HITL 保持 `question: allow` |
| `skills/*/SKILL.md` | **必改。** 模型按这个调工具 | HITL / 附件 / KB 的「何时调用」写在这里 |
| `.env` | 配 `{PKG}_*`，改完**重启 MCP** | 只决定「模块是否启用 / 工具是否注册」 |
| `__PKG_NAME__/hitl.py` | 一般不用改 API | **必须**在你的工具里自己列出 `missing`；演示 ping 的「空 message」不能当业务规则 |
| `__PKG_NAME__/attachments.py` | 一般不用改 | 新工具要自己声明 `attachment_refs_json`，pipeline 里调 `summarize_refs` / `load_excerpts` |
| `__PKG_NAME__/llm.py` | 一般不用改 | **不会自动调模型。** 在 pipeline 里自己 `chat_completion` / `complete_json` |
| `__PKG_NAME__/kb.py` | 协议变了才改 | 四项配齐**自动注册** `kb_search`；流水线内检索再调 `search()` |
| `__PKG_NAME__/output.py` | 一般不用改 | COS 配齐**自动注册** `emit_file`；或不配 COS，业务 JSON 仍可带 `files[].content_base64` |
| `__PKG_NAME__/progress.py` | 一般不用改 | 长步骤自己 `progress_fn("阶段名")`，否则前端看不到 progress |
| `__PKG_NAME__/config.py` | 加业务密钥时扩 Settings | 可选能力开关已接好，勿删 |
| `__PKG_NAME__/agent_card.py` | **不要改** | 打开 KB / output 时会自动给对应工具 `allow` |
| `__PKG_NAME__/logtrace.py` / `bizerror.py` | **不要改**（禁止 `import sleuth`） | 行内追踪按本包 / Sleuth 的 log_trace 配即可 |
| `__PKG_NAME__/__main__.py` | 不要改 | — |

最小顺序：`pipeline.py` 写业务 → `mcp_server.py` 注册工具 → `agent.md` 权限 → `SKILL.md` SOP → 重启 MCP → Sleuth **新开会话**验证。

### 0.2 演示 `_ping_payload`：不要照抄 missing

脚手架 HITL 例子是：`message` 为空 → `missing = ["回显文本 message"]` → 返回 `need_input`，不进 `pipeline.ping`。这只是连通性演示。

真实业务必须自己定义缺项，例如「无报告正文且无附件」「缺风险点编码」。`{PKG}_HITL=1` **不会**自动知道你的字段。你不改包装函数，HITL 永远只认「空 message」。

Sleuth **不解析** `need_input`。停车靠 SOP 让模型调内置 `question`。多个环节要人介入：多次返回不同的 `missing`，或 SOP 直接让模型调 `question`。不要在 MCP 里阻塞等待。

### 0.3 只开配置 vs 还要二次开发

| 能力 | `.env` | 开了之后自动发生的 | 你还要改的 |
|------|--------|-------------------|------------|
| 会话摘录 | `{PKG}_ATTACHMENTS=1` | **仅演示 ping** 多一个 `attachment_refs_json` | **新业务工具必须自己声明该参数**，用 `summarize_refs`。不要解 SM4 |
| 人工介入 | `{PKG}_HITL=1` | 不注册新工具；演示 ping 空 message 返回 `need_input` | **必须**自己算 `missing`；SOP 写清调 `question` |
| 本包 LLM | `{PKG}_LLM_*` 三项 | 什么工具都不会自动调 | 在 pipeline 里调用 `llm.py`；未配齐时用 `sleuth_llm_json` |
| 知识库 | 四项 `{PKG}_KB_*` | **自动注册** `kb_search` | 多数只改 SOP；流水线内检索再调 `search()` |
| 回传文件工具 | 本包 COS 配齐 | **自动注册** `emit_file` | 更推荐业务 JSON 带 `files[].content_base64`，不必配本包 COS |
| MCP 鉴权 | `{PKG}_MCP_TOKEN` | 自动装中间件 | 只配 env |
| 进度 | 无单独开关 | `progress.py` 已拷入 | 长步骤自己调 `progress_fn` |

「配齐后重启即可」只适用于：`kb_search`、`emit_file`、MCP token、演示 ping 的附件入参 / 空 message HITL。
**LLM、真业务 HITL、新工具读附件、进度条，都必须改代码。**

## 1. 启动工具面

```powershell
cd <this-package>
py -3.12 -m pip install -e ".[mcp]"
# 若要用 emit_file 上传 COS：py -3.12 -m pip install -e ".[mcp,cos]"
Copy-Item .env.example .env
py -3.12 -m __PKG_NAME__.mcp_server
```

默认 `http://127.0.0.1:__MCP_PORT__/mcp`。探活：`GET http://127.0.0.1:__MCP_PORT__/health`（即使配了 MCP token 也始终开放）。

## 2. 配置 Sleuth

把 [`deploy/sleuth.env.snippet`](deploy/sleuth.env.snippet) 粘进 Sleuth 工作目录 `.env`。

`agent:true` 会调用 `get_agent_card`，注册人格 `__AGENT_NAME__`。`--tools-only` 生成时 snippet 为 `agent:false`（工具对所有会话可见，不注册专用人格）。

- **私有 SOP**：Card 带 `skills[].content`，跟 Agent 走，不必单独 skill grant。
- **复用 COS 上已有 SOP**：在 `agent.md` 写 `catalog_skills:` 只填 name（不要建空 `SKILL.md`）。Sleuth 进程用 `SLEUTH_SKILLS_S3` 拉包；本 MCP **不**拉 skill。缺目录时跳过注入，不崩。
- 本地 `skills/` 同名且有正文时，覆盖 COS/路径同名条目。

客户端与本包共用一个 Bearer 时：Sleuth 设 `SLEUTH_MCP_HEADERS={"Authorization":"Bearer <token>"}`（或写在该 server 的 `headers`），本包设 `__ENV_PREFIX___MCP_TOKEN=<token>`。

## 3. 岗位授权（若 `SLEUTH_ACL_ENABLED=1`）

`PUT /v1/directory/grants` 使用 [`deploy/grant.example.json`](deploy/grant.example.json)。

- 专用 Agent：至少一条 `resource_kind=agent`、`resource_id=__AGENT_NAME__`。
- COS 共享 skill 还要给 **build** 选择器看见时，再加 `resource_kind=skill`、`resource_id=<catalog-skill-name>`。私有 Card SOP 不需要 skill grant。

## 4. 运行

```powershell
py -3.12 -m sleuth --agent __AGENT_NAME__
```

HTTP：`POST /v1/sessions` body `{ "agent": "__AGENT_NAME__" }`。

示例话术：请用 ping 回显「脚手架已接通」。

## 5. 会话文件与可选 JSON 约定

文件解析统一在 Sleuth：上传进会话邮箱后，基座解密并抽出 excerpt（PDF/xlsx/docx/图片视觉）。本 MCP 进程默认拿不到密文，也不该自己解 SM4。Sleuth 不解析你们的 markdown / LangGraph；工具返回值是字符串。只有希望基座帮你做 UI/邮箱时，才用可选顶层 JSON：

| 约定 | 谁用 | 不遵守会怎样 |
|------|------|----------------|
| 入参 `attachment_refs_json` | 要读会话附件 | 收不到摘录 |
| 入参 `sleuth_llm_json` | 内部 LLM 未配齐时用会话模型 | 直连 MCP 且本包 LLM 为空则调用失败 |
| 出参 `sources[]`（`title` + `http(s) url`） | 答复末尾灰色「知识来源」 | 不附来源段 |
| 出参 `files[]`（`content_base64` 或已有 `https url` / `object_key`） | Sleuth 加密写入会话邮箱，进 `done.files` | 前端收不到回传文件 |

禁止 data-URL / file-URL。

本包能力默认**生成代码、按 env 注册**（空 env 不注册空工具）。下表「未配齐」只说开关效果；**二次开发还要不要改代码见第 0.3 节**：

| 能力 | 本包 `.env` | 未配齐时 | 开了还要改代码吗 |
|------|-------------|---------|------------------|
| 会话摘录 | `__ENV_PREFIX___ATTACHMENTS=1` | `ping` 不声明 `attachment_refs_json` | 要。新工具自己声明该参数 |
| 内部 LLM | `__ENV_PREFIX___LLM_BASE_URL` + `_API_KEY` + `_MODEL` | 用 Sleuth 注入的会话模型；两头都空则业务 LLM 失败 | 要。pipeline 里自己调用 |
| 知识库 | `__ENV_PREFIX___KB_API_URL` + `_LOGIN_URL` + `_OPENID` + `_SERVICEID` | 不注册 `kb_search` | 一般不用。自动注册工具 |
| 回传文件 MCP 工具 | COS：access + secret + bucket + (region 或 endpoint) | 不注册 `emit_file`；业务 JSON 仍可带 `files[].content_base64` | 一般不用。自动注册；或只用 JSON `files[]` |
| 人工介入 | `__ENV_PREFIX___HITL=1` | 缺料不返回 `need_input`；基座不暂停 | 要。自己定义 `missing`，不要抄空 message |
| HTTP 鉴权 | `__ENV_PREFIX___MCP_TOKEN` 非空 | 不装中间件 | 不用。只配 env |

知识库、生成文件也可以不写进 MCP：会话里仍有 Sleuth 内置 `kb_lookup` / `save_output_file`（Card 权限可 deny 藏掉）。

## 6. 无 Card 回退

省略 `agent:true` 时只挂工具。可继续用本地 `agent.md` + `SLEUTH_SKILLS_PATHS=<this-package>/skills`。
