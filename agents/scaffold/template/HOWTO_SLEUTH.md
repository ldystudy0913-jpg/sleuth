# 将 __AGENT_NAME__ 挂到 Sleuth

当前生成模式：`__SKILL_MODE__`（`private` 嵌入 SOP / `cos` 点名 COS skill / `both` / `none` 仅工具）。

## 1. 启动工具面

```powershell
cd <this-package>
py -3.12 -m pip install -e ".[mcp]"
py -3.12 -m __PKG_NAME__.mcp_server
```

默认 `http://127.0.0.1:__MCP_PORT__/mcp`。探活：`GET http://127.0.0.1:__MCP_PORT__/health`。

## 2. 配置 Sleuth

把 [`deploy/sleuth.env.snippet`](deploy/sleuth.env.snippet) 粘进 Sleuth 工作目录 `.env`。

`agent:true` 会调用 `get_agent_card`，注册人格 `__AGENT_NAME__`。

- **private**：Card 带 `skills[].content`，跟 Agent 走，不必单独 skill grant。
- **cos / both**：把 `skills_cos/__COS_SKILL__/SKILL.md` 传到 COS（[`deploy/cos/README.md`](deploy/cos/README.md)），并配置 `SLEUTH_SKILLS_S3`。
- **none**：snippet 使用 `agent:false`，工具对所有会话可见，不注册专用人格。

## 3. 岗位授权（若 `SLEUTH_ACL_ENABLED=1`）

`PUT /v1/directory/grants` 使用 [`deploy/grant.example.json`](deploy/grant.example.json)。

- 专用 Agent：至少一条 `resource_kind=agent`、`resource_id=__AGENT_NAME__`。
- COS 共享 skill 还要给 **build** 选择器看见时，再加 `resource_kind=skill`、`resource_id=__COS_SKILL__`。私有 Card SOP 不需要 skill grant。

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
| 出参 `sources[]`（`title` + `http(s) url`） | 答复末尾灰色「知识来源」 | 不附来源段 |
| 出参 `files[]`（`filename` + `https url` 或 `object_key`） | 登记为助手文件，进 `done.files` | 前端收不到回传文件 |

禁止 data-URL / file-URL。知识库、生成文件也可以不写进 MCP：会话里仍有 Sleuth 内置 `kb_lookup` / `save_output_file`（Card 权限可 deny 藏掉）。

本次生成开关：

__OPTIONAL_HOWTO_FLAG_NOTES__

## 6. 无 Card 回退

省略 `agent:true` 时只挂工具。可继续用本地 `agent.md` + `SLEUTH_SKILLS_PATHS=<this-package>/skills`。
