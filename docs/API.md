# Sleuth HTTP API 接口文档（前端对接）

> 基于当前实现：[`sleuth/server/app.py`](../sleuth/server/app.py)  
> 默认基址：`http://127.0.0.1:8787`（`SLEUTH_SERVER_HOST` / `SLEUTH_SERVER_PORT`）  
> Content-Type：除非另有说明，请求/响应均为 `application/json; charset=utf-8`

CLI 交互与下列能力对齐（斜杠命令）：`/sessions` `/session` `/model` `/agent` `/mcp` `/skill` `/skills` `/usage` `/yolo` — 目录字段与 `GET /v1/models|agents|mcp|skills` 同源（[`sleuth/catalog.py`](../sleuth/catalog.py)）。

---

## 1. 启动服务

```powershell
cd <sleuth 仓库根目录>
py -3.12 -m pip install -e ".[server]"   # 或 .[all]
py -3.12 -m sleuth.server
```

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `SLEUTH_SERVER_HOST` | `127.0.0.1` | 监听地址 |
| `SLEUTH_SERVER_PORT` | `8787` | 端口 |
| `SLEUTH_SERVER_ADMIN_TOKEN` | 空 | 管理接口口令；**为空则不做 admin 校验** |
| `SLEUTH_SERVER_DEFAULT_BACKEND` | `sqlite` | 服务端默认存储 |
| `SLEUTH_TIMEZONE` | `Asia/Shanghai` | 列表里 `time_updated_local` 的时区 |

---

## 2. 通用约定

### 2.1 用户隔离

几乎所有会话接口按 **用户** 隔离。用户 ID 解析优先级：

1. 请求头 `X-User-Id`
2. Query：`?user_id=`
3. 默认：`anonymous`

创建会话时 body 里的 `user_id` 优先于上述 header/query。

前端建议：登录后固定把业务用户 id 放进 `X-User-Id`。

### 2.2 管理鉴权

部分接口需要请求头：

```http
X-Admin-Token: <与 SLEUTH_SERVER_ADMIN_TOKEN 相同>
```

若服务端 **未配置** `SLEUTH_SERVER_ADMIN_TOKEN`，则管理校验关闭（开发方便，生产务必配置）。

### 2.3 错误响应

统一为 JSON：

```json
{ "error": "说明文字" }
```

常见 HTTP 状态码：

| 状态码 | 含义 |
|--------|------|
| `200` | 成功 |
| `400` | 参数错误（如缺 prompt、非法 model） |
| `401` | Admin Token 不匹配 |
| `404` | 会话不存在，或不属于当前用户 |
| `413` | 上传文件过大或会话文件数超限 |
| `503` | 未配置 COS（会话文件邮箱不可用），或长期记忆未就绪（OpenGauss 驱动/表；见 `detail`） |

### 2.4 重要限制（对接必读）

- **会话主键**：创建/列表/详情响应字段名为 **`id`**；发消息回包与 SSE 事件字段名为 **`session_id`**。二者是**同一值**（如 `sess_a1b2…`），前端统一存成 `sessionId` 即可。多会话时所有读写必须带该 id（URL 路径），并固定 `X-User-Id`。
- **同步发消息**：`POST .../messages` 会阻塞到整轮 Agent（含工具调用）跑完再返回整包 JSON。前端需设足够长的超时（建议 ≥ 5–15 分钟），并做好 loading。
- **流式发消息**：`POST .../messages/stream` 使用 **SSE**（`text/event-stream`），边跑边推 `text` / 工具事件，最后以 `done` 收尾。**每条 `data` 事件均含 `session_id`**。原生 `EventSource` 只支持 GET，请用 `fetch` + `ReadableStream`。
- **模型 / Agent / Skill 选择**：每次创建会话和发消息都应带 `agent`、`model`、`skills`（推荐）或旧字段 `skill`。未选 agent / model 时传 `GET /v1/agents`、`GET /v1/models` 的 `default`；未选 skill、或当前不是默认 agent 时传 `skills: []` 且 `skill: ""`。Skill **仅当 `agent` 等于默认 agent** 时可选并注入 SKILL.md（可同时钉多个，按数组顺序注入全文）；专用 agent 带非空 `skills` / `skill` 返回 `400`。两个字段都出现时以 `skills` 为准。字段省略时兼容旧客户端（沿用会话已存值），前端主路径不要依赖省略。MCP 可晚于 Sleuth 启动，后台会重试；也可 `POST /v1/mcp/reload` 立即重连后再拉 agents。
- **Agent 型 MCP 隔离**：`agent:true` 的 MCP 工具只在当前会话就是该 agent、且用户有对应 agent grant 时可见/可执行。`yolo` 只跳过 bash/edit 确认，**不能**让 `build` 调用尽调工具。通用 MCP（`agent:false`）仍挂在所有 agent 上。
- **`GET /v1/skills`**：返回当前用户可见的目录 skill（`pinnable: true`，走 skill grant）以及有权限的 agent 私有 skill（`pinnable: false`）。前端只用 `pinnable: true` 填充 build 选择器。
- **无 CORS 中间件**：浏览器跨域需自行在网关加 CORS，或同域反代。
- **会话 id** 形如：`sess_` + 24 位 hex（例：`sess_a1b2c3d4e5f678901234abcd`）。
- 时间字段：
  - `time_updated`：Unix **毫秒**
  - `time_updated_local`：按 `timezone` / `SLEUTH_TIMEZONE` 格式化的字符串，如 `2026-08-08 18:09:12`
  - 台账 `started_at` 等仍为毫秒数字；同时提供 `started_at_iso` / `ended_at_iso` / `completed_at_iso` / `first_token_at_iso`（ISO-8601 字符串），避免 13 位数字被网关当卡号屏蔽。

### 2.5 `model` 对象形态（注意两处略有不同）

**创建会话 / 发消息** 成功响应里的 `model`：

```json
{
  "ref": "deepseek-chat/deepseek-chat",
  "id": "deepseek-chat",
  "providerID": "deepseek-chat"
}
```

**列表 / 详情** 里落库的 `model` 多为：

```json
{
  "id": "deepseek-chat",
  "providerID": "deepseek-chat"
}
```

`model` 请求参数可为：

- `SLEUTH_MODELS` 里的 alias（如 `qwen-max`）
- 或 `provider/model`（如 `openai/gpt-4o-mini`）

---

## 3. 接口一览

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `GET` | `/health` | 无 | 探活 |
| `POST` | `/v1/sessions` | 用户头 | 创建会话 |
| `GET` | `/v1/sessions` | 用户头 | 会话列表（含预览） |
| `GET` | `/v1/sessions/{session_id}` | 用户头 | 会话详情 + 消息 |
| `GET` | `/v1/sessions/{session_id}/trace` | 用户头 | 会话执行台账（轮次 / 工具 / 计时） |
| `POST` | `/v1/sessions/{session_id}/files` | 用户头 | multipart 上传明文（服务端 SM4 后写入 COS） |
| `GET` | `/v1/sessions/{session_id}/files` | 用户头 | 列出会话文件 |
| `GET` | `/v1/sessions/{session_id}/files/{file_id}` | 用户头 | 下载**明文**（`?inline=1` 可预览） |
| `DELETE` | `/v1/sessions/{session_id}/files/{file_id}` | 用户头 | 删除元数据与 COS 对象 |
| `POST` | `/v1/sessions/{session_id}/files/uploads` | 用户头 | **已废弃** `410`（原预签名 PUT） |
| `POST` | `/v1/sessions/{session_id}/files/complete` | 用户头 | **已废弃** `410` |
| `POST` | `/v1/sessions/{session_id}/messages` | 用户头 | 发送一轮对话（**同步 JSON**） |
| `POST` | `/v1/sessions/{session_id}/messages/stream` | 用户头 | 发送一轮对话（**SSE 流式**） |
| `GET` | `/v1/models` | 无 | 模型目录（选择器用；不含密钥） |
| `GET` | `/v1/agents` | 用户头 | Agent 目录（开启 ACL 时按岗位授权过滤） |
| `GET` | `/v1/mcp` | 无 | MCP 服务连接状态 |
| `POST` | `/v1/mcp/reload` | Admin | 热重载 MCP（重连 + 刷新 Agent Card） |
| `GET` | `/v1/users/{user_id}/usage` | 本人或 Admin | 用量汇总 |
| `GET` | `/v1/skills` | 用户头 | Skill 目录（开启 ACL 时按岗位授权过滤） |
| `POST` | `/v1/skills/reload` | Admin | 强制重载 Skill |
| `GET` | `/v1/memory` | 用户头 | 列出或向量检索当前用户可见记忆（`?q=` / `?kb_status=`） |
| `POST` | `/v1/memory` | 用户头 | 写入记忆（默认 user 层；role/org 需 Admin） |
| `PATCH` | `/v1/memory/{memory_id}` | 用户头 | 更新正文并重算向量，或改 `kb_status` / `kb_ref` |
| `DELETE` | `/v1/memory/{memory_id}` | 用户头 | 归档（忘记） |
| `GET`/`PUT` | `/v1/directory/users/{user_id}` | Admin | 维护一人一岗一机构 |
| `GET`/`PUT` | `/v1/directory/grants` | Admin | 维护岗位/机构/用户例外授权 |

---

## 4. 接口详情

### 4.1 健康检查

`GET /health`

**请求**：无参数。

**响应 `200`**

```json
{ "ok": true }
```

---

### 4.2 创建会话

`POST /v1/sessions`

**Headers**

| Header | 必填 | 说明 |
|--------|------|------|
| `Content-Type` | 是 | `application/json` |
| `X-User-Id` | 建议 | 用户 id |

**Body**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `user_id` | string | 否 | 见 §2.1 | 覆盖 header 中的用户 |
| `agent` | string | 否 | 服务端 `default_agent`（常为 `build`） | Agent **id**（`GET /v1/agents` 的 `name`）。未选时传该接口的 `default` |
| `yolo` | boolean | 否 | `true` | `true` 时自动批准工具调用 |
| `model` | string | 否 | 配置默认模型 | 未选时传 `GET /v1/models` 的 `default` |
| `skills` | string[] | 否 | `[]` | 推荐。仅默认 agent 可非空；未选或专用 agent 时传 `[]`。按顺序把 SKILL.md 注入系统提示 |
| `skill` | string | 否 | `""`（无绑定） | 兼容旧客户端的单选；与 `skills` 同时出现时以 `skills` 为准。`""` 清空 |

**请求示例**

```http
POST /v1/sessions HTTP/1.1
Host: 127.0.0.1:8787
Content-Type: application/json
X-User-Id: alice

{
  "agent": "build",
  "yolo": true,
  "model": "qwen-max",
  "skills": [],
  "skill": ""
}
```

**响应 `200`**

```json
{
  "id": "sess_a1b2c3d4e5f678901234abcd",
  "user_id": "alice",
  "title": "New session - 2026-08-08 18:09:12",
  "agent": "build",
  "model": {
    "ref": "qwen-max/qwen-max",
    "id": "qwen-max",
    "providerID": "qwen-max"
  },
  "skill": null,
  "skills": []
}
```

响应里的 **`id` 即会话主键**，与发消息/SSE 中的 `session_id` 相同。

**错误**

| 状态 | body |
|------|------|
| `400` | `{ "error": "invalid model: ..." }` |
| `400` | `{ "error": "invalid skill: ..." }` |
| `400` | `{ "error": "skill only allowed when agent is the default agent" }` |

---

### 4.3 会话列表

`GET /v1/sessions`

**Headers / Query**

| 参数 | 位置 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `X-User-Id` / `user_id` | header / query | 建议 | `anonymous` | 只返回该用户会话 |
| `limit` | query | 否 | `50` | 条数，服务端会夹在 1–100 |

**请求示例**

```http
GET /v1/sessions?limit=20 HTTP/1.1
X-User-Id: alice
```

**响应 `200`**：JSON **数组**（按 `time_updated` 新→旧）

```json
[
  {
    "id": "sess_bbb222...",
    "user_id": "alice",
    "title": "尽调报告检查 - XX银行",
    "agent": "dd_check",
    "model": { "id": "qwen-max", "providerID": "qwen-max" },
    "skill": null,
    "skills": [],
    "tokens_input": 1200,
    "tokens_output": 340,
    "time_updated": 1723123456789,
    "time_updated_local": "2026-08-08 18:09:12",
    "preview": "请检查下面这份尽调报告…"
  }
]
```

| 字段 | 说明 |
|------|------|
| `preview` | 该会话**首条用户消息**截断预览（约 80 字）；尚无用户消息则为 `""` |
| `time_updated_local` | 本地时区可读时间，便于 UI 直接展示 |
| `title` | 默认带创建时间；首轮对话后可能被模型改写为语义标题 |

---

### 4.4 会话详情（含消息）

`GET /v1/sessions/{session_id}`

**路径参数**：`session_id` — 会话 id。

**Headers**：`X-User-Id`（必须能匹配该会话归属用户，否则 404）。

**响应 `200`**

```json
{
  "id": "sess_a1b2c3d4e5f678901234abcd",
  "user_id": "alice",
  "title": "尽调报告检查 - XX银行",
  "agent": "dd_check",
  "model": { "id": "qwen-max", "providerID": "qwen-max" },
  "skill": null,
  "skills": [],
  "cost": 0.045,
  "tokens": {
    "input": 5000,
    "output": 1200,
    "reasoning": 0,
    "cache_read": 0,
    "cache_write": 0
  },
  "messages": [
    {
      "id": "msg_...",
      "role": "user",
      "text": "请检查这份尽调报告…",
      "usage": null,
      "cost": null
    },
    {
      "id": "msg_...",
      "role": "assistant",
      "text": "检查结论：…",
      "usage": { "input": 100, "output": 50 },
      "cost": 0.001,
      "step": 1,
      "started_at": 1723123456789,
      "first_token_at": 1723123457010,
      "completed_at": 1723123457989,
      "duration_ms": 1200
    }
  ]
}
```

| `messages[].role` | 含义 |
|-------------------|------|
| `user` | 用户 |
| `assistant` | 助手（`text` 为拼接后的可见文本；含工具轮次时可能较碎，以落库为准） |
| `tool` | 工具结果消息（若存在；`text` 为工具输出摘要） |

`messages[]` 还可带 `step` / `started_at` / `first_token_at` / `completed_at` / `duration_ms`（Unix **毫秒**）。旧会话没有计时时这些字段为 `null`；忽略未知字段的旧客户端不受影响。完整工具台账（含 `id` / 耗时）见 §4.4.1。

**错误**

| 状态 | body |
|------|------|
| `404` | `{ "error": "not found" }` |

---

### 4.4.1 会话执行台账（Trace）

`GET /v1/sessions/{session_id}/trace`

**说明**：从已落库消息投影 Trajectory 风格台账（不新建表）。前端用 `records` 画轮次列表；时间条用 `started_at` + `duration_ms`。旧会话缺计时字段为 `null`，按「未知时长」处理。**进行中的 span 不会编造 duration**（实时路径见 SSE，只有 start、没有 duration）。

**Headers**：`X-User-Id`（须匹配会话归属用户，否则 404）。

**响应 `200`**

```json
{
  "session_id": "sess_a1b2c3d4e5f678901234abcd",
  "records": [
    {
      "kind": "user",
      "seq": 1,
      "message_id": "msg_...",
      "started_at": 1723123456789,
      "preview": "请检查…"
    },
    {
      "kind": "message",
      "seq": 2,
      "message_id": "msg_...",
      "step": 1,
      "started_at": 1723123456900,
      "first_token_at": 1723123457120,
      "completed_at": 1723123458100,
      "duration_ms": 1200,
      "usage": { "input": 100, "output": 40 },
      "preview": "正在检查…"
    },
    {
      "kind": "tool",
      "seq": 3,
      "message_id": "msg_...",
      "id": "call_...",
      "name": "ddreply_generate_reply_framework",
      "started_at": 1723123458100,
      "duration_ms": 800,
      "ended_at": 1723123458900,
      "is_error": false,
      "preview": "ddreply_generate_reply_framework"
    }
  ]
}
```

| `kind` | 含义 |
|--------|------|
| `user` | 用户输入 |
| `message` | 助手一轮模型流（含 TTFT） |
| `tool` | 一次工具执行 |

时间字段均为 Unix **毫秒**。`preview` 已截断并按服务端脱敏策略处理。

#### 与 Trajectory 字段对照

| Trajectory | Sleuth | 说明 |
|------------|--------|------|
| `startedAt` | `started_at` | 开始时间（ms） |
| `timeSeconds` | `duration_ms / 1000` | 完成后再有时长；运行中不要虚构 |
| TTFT | `first_token_at - started_at` | 仅 `kind=message`；缺一则为未知 |
| `kind` | `kind` | `user` / `message` / `tool` |

实时流式时：用 SSE 的 `step` / `tool_start` / `tool_result` / `stop` 追加行；结束后再用本接口对齐历史。

**错误**

| 状态 | body |
|------|------|
| `404` | `{ "error": "not found" }` |

---

### 4.4.2 会话文件（COS 邮箱）

默认 agent 是 `build`。用户**不必切换**到 `dd_reply` 等专用 agent 也能：上传附件、把生成文件回传给前端、检索远程知识库（`kb_lookup`，需 `SLEUTH_KB_API_URL` + `SLEUTH_KB_LOGIN_URL` + `SLEUTH_KB_OPENID` + `SLEUTH_KB_SERVICEID`）。

字节**经过** Sleuth：前端 `POST` multipart **明文**；Sleuth 用本机 `SLEUTH_SM4_KEY` 做 SM4-CBC（key == IV，PKCS7）后写入 COS。COS 只存密文。`GET` 同一路径时进程内解密，响应为明文（`Content-Type` 为原始 mime）。明文不落盘、不写 SSE。前端**不做** SM4。Agent 只用摘录，不要把 COS URL 交给模型。

可调项全部在 `FilesConfig`（默认）/ `.env` 的 `SLEUTH_FILES_*` / `sleuth.jsonc` 的 `files`，不要改业务代码。常用：`SLEUTH_SM4_KEY`、`SLEUTH_FILES_REQUIRE_ENCRYPT`、`SLEUTH_FILES_MAX_BYTES`、`SLEUTH_FILES_UPLOAD_FORM_FIELD`（默认 `file`）、`SLEUTH_FILES_DOWNLOAD_PATH_TEMPLATE`、`SLEUTH_FILES_INLINE_QUERY_PARAM`（默认 `inline`）。

配置：会话文件与 Skill 共用 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` / `SLEUTH_S3_ENDPOINT`，桶名取 `SLEUTH_SKILLS_S3` 的 `s3://桶/...`。另加 `SLEUTH_COS_PATH_PREFIX`（默认 `sleuth/files`）。对象存储客户端随核心依赖安装（boto3）。PDF/xlsx/docx 解析与扫描 PDF 渲染：`pip install sleuth[files]`（含 pypdfium2）；图片 RapidOCR 回退：`pip install sleuth[ocr]`。图片默认走多模态视觉抽取（`SLEUTH_FILES_IMAGE_MODE=vision`）：先描述场景再列可见文字（`SLEUTH_FILES_VISION_PROMPT`）。无文字层的 PDF 会逐页渲染后走同一视觉/OCR（页数/DPI：`SLEUTH_FILES_PDF_VISION_MAX_PAGES` / `SLEUTH_FILES_PDF_RENDER_DPI`）。对话模型看到的是 excerpt，不是原图像素；excerpt 不够时对 `read_session_file` 传入用户原话 `question` 会再解析，不覆盖已存 excerpt。看图需视觉模型（可配 `SLEUTH_FILES_VISION_MODEL`）；纯 OCR 回退仍可能只有字。

#### 上传

`POST /v1/sessions/{session_id}/files`（`multipart/form-data`，字段名见 `SLEUTH_FILES_UPLOAD_FORM_FIELD`，默认 `file`）

**响应 `200`**

```json
{
  "id": "file_...",
  "filename": "notes.txt",
  "mime": "text/plain",
  "size": 5,
  "role": "user",
  "status": "ready",
  "download_url": "/v1/sessions/sess_.../files/file_...",
  "excerpt_status": "pending",
  "encrypted": true
}
```

`download_url` 是 **Sleuth API 路径**，不是 COS URL。前端带 `X-User-Id` 访问即可拿到明文。配了 `SLEUTH_FILES_REQUIRE_ENCRYPT=1` 但未配 `SLEUTH_SM4_KEY` 时返回 `503`。

| 状态 | 说明 |
|------|------|
| `413` | 超过 `SLEUTH_FILES_MAX_BYTES`，或会话文件数超过 `SLEUTH_FILES_MAX_COUNT` |
| `503` | 未配置共享 COS，或强制加密但未配 SM4 密钥 |

`POST .../files/uploads` 与 `POST .../files/complete` 返回 **`410`**，文案见 `SLEUTH_FILES_DEPRECATED_PRESIGN_MESSAGE`。

抽取在进程内限流队列中进行（`SLEUTH_FILES_EXTRACT_CONCURRENCY`），完成后 `excerpt_status` 为 `ok` 或 `skipped`。`done` / system prompt 使用的是摘录，不是 COS 明文。

#### 列表 / 下载 / 删除

`GET /v1/sessions/{session_id}/files` → `{ "files": [{ "id", "filename", "mime", "size", "role", "status", "download_url", "excerpt_status", "encrypted" }] }`。`?include_pending=1`（参数名可配 `SLEUTH_FILES_INCLUDE_PENDING_QUERY`）会带上未 ready 的项。

`GET /v1/sessions/{session_id}/files/{file_id}`：返回**明文字节**。默认 `Content-Disposition: attachment`。`?inline=1`（真值列表见 `SLEUTH_FILES_INLINE_QUERY_TRUTHY`）改为 inline，便于预览。

`DELETE /v1/sessions/{session_id}/files/{file_id}`：删会话元数据并删 COS 对象。

`download_url` 在列表 / `done.files` 里同样是 Sleuth API 路径。

---

### 4.5 发送消息（一轮 Agent）

`POST /v1/sessions/{session_id}/messages`

**说明**：同步执行完整 agent 循环（可能多次工具调用），**耗时长**。成功后返回本轮助手最终文本。

**Headers**

| Header | 必填 |
|--------|------|
| `Content-Type: application/json` | 是 |
| `X-User-Id` | 是（须与会话用户一致） |

**Body**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `prompt` | string | 二选一 | — | 用户输入（推荐） |
| `text` | string | 二选一 | — | 与 `prompt` 等价，兼容字段 |
| `agent` | string | 否 | 未选时传默认 agent | 每轮带当前选择器值 |
| `yolo` | boolean | 否 | `true` | 是否自动批准工具 |
| `model` | string | 否 | 未选时传默认模型 | 每轮带当前选择器值 |
| `skills` | string[] | 否 | `[]` | 推荐；仅默认 agent 可非空，否则传 `[]` |
| `skill` | string | 否 | `""` | 兼容单选；与 `skills` 同时出现时以 `skills` 为准 |
| `file_ids` | string[] | 否 | 本会话全部 `ready` 文件 | 本轮交给 MCP 的附件；`[]` 表示本轮不用附件 |

**请求示例**

```http
POST /v1/sessions/sess_a1b2c3d4e5f678901234abcd/messages HTTP/1.1
Content-Type: application/json
X-User-Id: alice

{
  "prompt": "请检查下面这份尽调报告：\n{...}",
  "agent": "build",
  "model": "qwen-max",
  "skills": ["dd-check-sop"],
  "skill": "dd-check-sop",
  "yolo": true
}
```

**响应 `200`**

```json
{
  "session_id": "sess_a1b2c3d4e5f678901234abcd",
  "text": "综合得分 72（等级 C）；主要问题：…",
  "title": "尽调报告检查 - XX银行",
  "agent": "build",
  "model": {
    "ref": "qwen-max/qwen-max",
    "id": "qwen-max",
    "providerID": "qwen-max"
  },
  "skill": "dd-check-sop",
  "skills": ["dd-check-sop"],
  "files": [],
  "usage": {
    "input": 1200,
    "output": 400,
    "reasoning": 0,
    "cache_read": 0,
    "cache_write": 0
  },
  "cost": 0.0123
}
```

| 字段 | 说明 |
|------|------|
| `text` | 本轮助手对用户可见的最终文本 |
| `status` | `ok` 正常结束；`awaiting_user` 表示本轮在 `question` 上暂停，等用户下一条消息 |
| `questions` | 仅 `awaiting_user`：列出缺项并询问是否还有补充（`question` / `options`） |
| `usage` | **本轮最后一次**模型调用用量（非整会话累加） |
| `cost` | 会话累计费用估算 |
| `title` | 可能在首轮后更新为语义标题 |
| `files` | 本轮**新产生**的助手文件（`id` / `filename` / `mime` / `size` / `download_url`）；无则 `[]` |
| `skills` | 当前绑定的 skill 名列表；未选为 `[]` |
| `skill` | 兼容字段，等于 `skills[0]`；未选为 `null` |

**错误**

| 状态 | body |
|------|------|
| `400` | `{ "error": "invalid json" }` |
| `400` | `{ "error": "prompt required" }` |
| `400` | `{ "error": "invalid model: ..." }` |
| `400` | `{ "error": "invalid skill: ..." }` |
| `400` | `{ "error": "skill only allowed when agent is the default agent" }` |
| `404` | `{ "error": "not found" }` |

---

### 4.5.1 发送消息（SSE 流式）

`POST /v1/sessions/{session_id}/messages/stream`

**说明**：与 §4.5 **同一套鉴权与 Body**，但响应为 **SSE**。模型侧 `text` 按增量推送；MCP/工具为同步等待，期间会先有 `tool_start`，结束后有 `tool_result`（中间可能长时间没有 `text`）。连接结束前必有一条 `type=done`（含完整 `text`，请以前端最终对齐为准）。

若 `done.status` 为 `awaiting_user`：本轮已停，请把 `questions`（或缺项说明）展示给用户。用户补充字段或回复「没有补充、继续」后再发一条 `POST .../messages`（不必新接口）。`stop.reason` 可能为 `ask`。

台账用 `step` / `tool_start` / `tool_result` / `stop` 追加行；时间条用 `started_at` + `duration_ms`。**运行中只有 start、没有 duration**（不要用墙钟时间填假长度）。`done` 仍作终态对齐，字段不变。旧事件 `type` 名与必填字段保持兼容，下列为新增可选字段。

**Headers**

| Header | 必填 | 说明 |
|--------|------|------|
| `Content-Type: application/json` | 是 | 请求体仍是 JSON |
| `X-User-Id` | 是 | 须与会话用户一致 |
| `Accept: text/event-stream` | 建议 | |

**Body**：同 §4.5（每轮带 `prompt` / `agent` / `model` / `skills` / 可选 `file_ids`）。

**成功时 HTTP `200`**，`Content-Type: text/event-stream`。

每帧格式（**每条 `data` 均含 `session_id`**）：

```text
data: {"type":"text","delta":"你好","session_id":"sess_a1b2c3d4e5f678901234abcd"}\n\n
```

心跳（可忽略，无 JSON）：

```text
: ping\n\n
```

#### 事件类型一览

所有下列事件（除心跳）都带 `session_id`。

| `type` | 字段 | 说明 |
|--------|------|------|
| `text` | `delta`, `session_id`；该步**首次**增量可带 `first_token_at` | 助手可见文本增量（拼起来即正文）；后续增量可省略 `first_token_at` |
| `reasoning` | `delta`, `session_id`；该步**首次**增量可带 `first_token_at` | 思考过程增量（可忽略） |
| `step` | `step`, `max_steps`, `session_id`；可选 `started_at` | Agent 循环步数 |
| `tool_start` | `name`, `args_preview`, `session_id`；可选 `id`, `step`, `started_at` | 开始调用工具（含 MCP）；`args_preview` 已截断。`id` 为 tool call id |
| `tool_result` | `name`, `is_error`, `output_preview`, `session_id`；可选 `id`, `step`, `started_at`, `duration_ms`, `ended_at` | 工具返回；`output_preview` 已截断。完成后才有 `duration_ms` |
| `retry` | `attempt`, `message`, `wait`, `session_id` | 模型调用重试 |
| `stop` | `reason`, `usage`, `session_id`；可选 `step`, `started_at`, `first_token_at`, `completed_at`, `duration_ms` | 单次模型流结束（一轮里可能多次） |
| `error` | `message`, `session_id` | 错误信息（连接可能仍继续到 `done`） |
| `done` | 见下表 | **收尾**；对齐完整结果 |

#### `done` 载荷

```json
{
  "type": "done",
  "session_id": "sess_a1b2c3d4e5f678901234abcd",
  "text": "综合得分 72（等级 C）；主要问题：…",
  "title": "尽调报告检查 - XX银行",
  "agent": "build",
  "model": {
    "ref": "qwen-max/qwen-max",
    "id": "qwen-max",
    "providerID": "qwen-max"
  },
  "skill": "dd-check-sop",
  "skills": ["dd-check-sop"],
  "files": [],
  "usage": {
    "input": 1200,
    "output": 400,
    "reasoning": 0,
    "cache_read": 0,
    "cache_write": 0
  },
  "cost": 0.0123
}
```

与同步接口字段对齐：`text` / `title` / `agent` / `model` / `skills` / `skill` / `files` / `usage` / `cost`。

#### 示例事件流（示意）

```text
data: {"type":"step","step":1,"max_steps":30,"started_at":1723123456900,"session_id":"sess_..."}

data: {"type":"text","delta":"正在","first_token_at":1723123457120,"session_id":"sess_..."}

data: {"type":"text","delta":"检查…","session_id":"sess_..."}

data: {"type":"tool_start","name":"ddcheck_check_report","id":"call_...","step":1,"started_at":1723123458100,"args_preview":"{...}","session_id":"sess_..."}

data: {"type":"stop","reason":"tool_use","usage":{...},"step":1,"started_at":1723123456900,"first_token_at":1723123457120,"completed_at":1723123458100,"duration_ms":1200,"session_id":"sess_..."}

data: {"type":"tool_result","name":"ddcheck_check_report","id":"call_...","is_error":false,"duration_ms":800,"ended_at":1723123458900,"output_preview":"{...}","session_id":"sess_..."}

data: {"type":"text","delta":"\n结论：得分 72","first_token_at":1723123459000,"session_id":"sess_..."}

data: {"type":"done","session_id":"sess_...","text":"正在检查…\n结论：得分 72","title":"...","model":{...},"usage":{...},"cost":0.01}
```

#### 与同步接口对比

| | `POST .../messages` | `POST .../messages/stream` |
|--|---------------------|----------------------------|
| 响应 | 一次 JSON（含 `session_id`） | SSE 多帧（每帧含 `session_id`）+ 最后 `done` |
| 打字机 | 否 | 是（拼 `text.delta`） |
| 工具进度 | 无 | `tool_start` / `tool_result`（含 `id` / 耗时） |
| 执行台账 | 结束后 `GET .../trace` | 流式用 `step`/`tool_*`/`stop`；历史用 `GET .../trace` |
| 记忆 / 用户隔离 | 相同 | 相同 |

#### 前端示例（`fetch` + ReadableStream）

```javascript
async function streamMessage(base, sessionId, userId, prompt) {
  const res = await fetch(`${base}/v1/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": userId,
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ prompt, yolo: true }),
  });
  if (!res.ok) throw new Error(await res.text());
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let full = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const chunk of parts) {
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.type === "text") {
        full += ev.delta || "";
        // update UI with full or ev.delta
      } else if (ev.type === "tool_start") {
        // show “工具执行中: ” + ev.name
      } else if (ev.type === "done") {
        full = ev.text || full; // 以 done.text 为准
        return ev;
      }
    }
  }
}
```

**错误（在建立流之前）**：与 §4.5 相同，返回普通 JSON（`400` / `404`），不是 SSE。

**客户端断开**：服务端会 `cancel` 当前轮，尽量停止后续模型/工具调用。

---

### 4.6 模型目录

`GET /v1/models`

**说明**：返回 `SLEUTH_MODELS` 配置的模型别名列表，供前端下拉。**不返回** `apiKey` 等密钥。切换模型仍用创建/发消息 body 的 `model` 字段（传这里的 `id`）。

**响应 `200`**

```json
{
  "default": "qwen-max",
  "models": [
    {
      "id": "qwen-max",
      "ref": "qwen-max/qwen-max",
      "label": "qwen-max @ https://dashscope.aliyuncs.com/compatible-mode/v1"
    },
    {
      "id": "deepseek-chat",
      "ref": "deepseek/deepseek-chat",
      "label": "deepseek/deepseek-chat"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `default` | 服务端默认模型（`SLEUTH_MODEL` / 配置），可能为 null |
| `models[].id` | 别名；发消息时 `model` 用此值 |
| `models[].ref` | 解析后的 `provider/model` |
| `models[].label` | 展示用标签（可能含 baseURL，不含密钥） |

目录为空时：`models` 为 `[]`，仍可返回 `default`。

---

### 4.7 Agent 目录

`GET /v1/agents`

**Query**

| 参数 | 默认 | 说明 |
|------|------|------|
| `include_hidden` | `false` | `1` / `true` / `yes` 时包含 `hidden` agent |

**说明**：枚举本地 + MCP Agent Card 注册的 agent。列表用 `title` 展示、用 `name` 切换。`default` 即主 agent；**仅该 agent 允许非空 `skills` / `skill`**。每轮发消息应带当前 `agent`。`ddreply` 这类 MCP 配置键会解析为 Card 上的规范名（如 `dd_reply`），切过去后系统提示词用该 agent 的人格，而不是默认 sleuth。

开启 `SLEUTH_ACL_ENABLED` 且目录表可用时，列表按 `X-User-Id` 查 `mem_user` 的唯一岗位/机构，再按 `mem_grant` 过滤：user deny → user allow → role/org allow → 默认 agent（若 `SLEUTH_ACL_DEFAULT_AGENT_OPEN`）。`resource_id` 等于 `acl.wildcard_id`（默认 `*`，env `SLEUTH_ACL_WILDCARD_ID`）时表示该 `resource_kind` 下全部名字，含以后新上的 agent/skill。无授权的 agent 前端选不了；`set_agent` / 发消息 body `agent` 同样拒绝。未建目录表或 ACL 关闭时保持全可见。有通配 agent grant 的用户仍须把会话切到对应 agent，才能看见/调用该 agent 的 MCP 工具；`yolo` 不能代替授权。

**响应 `200`**

```json
{
  "default": "build",
  "agents": [
    {
      "name": "build",
      "title": "通用助手",
      "description": null,
      "mode": "all",
      "hidden": false,
      "model": null,
      "source": "local",
      "mcp_server": null,
      "available": true
    },
    {
      "name": "dd_check",
      "title": "尽调报告检查分析师",
      "description": "对银行尽职调查报告做确定性检查与中文研判。",
      "mode": "primary",
      "hidden": false,
      "model": null,
      "source": "mcp",
      "mcp_server": "ddcheck",
      "aliases": ["ddcheck"],
      "available": true
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `name` | Agent id；创建会话 / 发消息 body 的 `agent` 用此值 |
| `title` | 展示名称。MCP Agent 来自 `agent.md` / Agent Card；未配置时 `build` 为「通用助手」，其它回退为 `name` |
| `description` | 列表副文案 / 能力说明 |
| `source` | `local` 或 `mcp` |
| `mcp_server` | 来自哪个 MCP 配置键；本地为 null |
| `aliases` | 也可用于 body `agent` 的别名（通常含 MCP 配置键） |
| `available` | MCP 源取决于该 server 当前是否已连接；本地恒 `true` |

---

### 4.7.1 MCP 状态与热重载

`GET /v1/mcp`

返回各 remote MCP 连接状态、已注册工具与 Agent Card 名（**不含密钥**）。

```json
{
  "servers": [
    {
      "name": "ddcheck",
      "url": "http://127.0.0.1:8791/mcp",
      "connected": true,
      "error": null,
      "agent": true,
      "agents": ["dd_check"]
    }
  ],
  "tools": ["ddcheck_check_report"],
  "agents": ["dd_check"],
  "errors": []
}
```

`POST /v1/mcp/reload`

**Headers**：`X-Admin-Token`（若配置了 admin token）。

并行重连所有 remote MCP，刷新工具与 Agent Card。未连上的服务会按 `SLEUTH_MCP_RETRY_SECONDS` 后台重试；也可随时 reload 立即重连。

单服务连接超时可由 `SLEUTH_MCP_TIMEOUT_PER_SERVER`（ms）控制；一个挂起不会阻塞其它服务。

---

### 4.8 用户用量汇总

`GET /v1/users/{user_id}/usage`

**鉴权**

- 请求用户（`X-User-Id`）与路径 `user_id` **相同** → 允许；
- 否则需有效 `X-Admin-Token`（若配置了 admin token）。

**响应 `200`**

```json
{
  "user_id": "alice",
  "events": 42,
  "tokens_input": 100000,
  "tokens_output": 25000,
  "tokens_reasoning": 0,
  "cost": 1.234
}
```

---

### 4.9 Skill 列表

`GET /v1/skills`

**响应 `200`**：数组

```json
[
  {
    "name": "kyc-shared",
    "description": "共享 KYC SOP",
    "location": ".../skills-cache/.../kyc-shared",
    "pinnable": true
  },
  {
    "name": "dd-reply-framework",
    "description": "尽调答复框架 SOP",
    "location": "mcp_agent/dd_reply/dd-reply-framework",
    "pinnable": false,
    "owner_agent": "dd_reply"
  }
]
```

触发懒惰刷新逻辑（受 `SLEUTH_SKILLS_REFRESH_SECONDS` 影响）。

默认 agent 下发消息 / 创建会话时，`skills` 只填 **`pinnable: true`** 的 `name`；未选传 `[]`。旧字段 `skill` 仍可用。`pinnable: false` 不要放进 build 选择器。

多份 SKILL.md 会按选择顺序全部注入系统提示，注意上下文长度。

---

### 4.10 强制重载 Skill

`POST /v1/skills/reload`

**Headers**：`X-Admin-Token`（若服务端配置了 admin token）。

**响应 `200`**

```json
{
  "ok": true,
  "count": 3,
  "names": ["dd-check-sop", "other-skill"]
}
```

**错误**：`401` `{ "error": "unauthorized" }`

---

### 4.11 长期记忆

记忆正文只存已脱敏文本；写入前若仍含未掩码证件号/手机/卡号则 `400` 且不写向量。会话不迁 OpenGauss。未配 `SLEUTH_MEMORY_BACKEND`、缺少 OpenGauss 驱动、或表不可达时接口返回 `503`，对话与 agent 授权不受影响。`503` 的 `detail` 为进程内真实原因。

驱动请装进**启动 HTTP 服务的同一个 Python**：在仓库根目录执行 `python -m pip install -e ".[memory]"`（或 `python -m pip install psycopg2-binary`）。不要对内网 PyPI 执行 `pip install sleuth[memory]`，本仓库未发布到该索引。装好后必须重启进程。

OpenGauss 列为 `FLOATVECTOR` 时设 `SLEUTH_MEMORY_VECTOR_KIND=floatvector`（召回用 `cosine_distance`，不要用 pgvector 的 `<=>`）；`body_text`/`payload_text` 为 JSONB 时设 `SLEUTH_MEMORY_TEXT_KIND=jsonb`。无向量索引不影响小数据量召回。SQL ANN 失败会回退到进程内余弦，写入不受影响。`SLEUTH_EMBEDDING_BASE_URL` 可填 OpenAI 兼容根路径（`.../v1`）或完整 embeddings 地址（`.../v1/embeddings`）；写入会 POST 到该地址一次，网关 404 时记忆不会落库。

手工建表与测试插入示例：[`docs/ddl_memory_opengauss.sql`](ddl_memory_opengauss.sql)（记忆）+ [`docs/ddl_memory_mysql.sql`](ddl_memory_mysql.sql)（目录/授权，与会话同库）。代码不执行这些 SQL。

`GET /v1/memory?q=` — `q` 为空则列出当前用户 user+role+org 可见的未过期条目；有 `q` 则向量检索。`?kb_status=` 按知识库收纳状态过滤（`none` / `nominated` / `ingested` / `stale`，词表见配置 `memory.kb_status_*`）。列表同时返回 `kb_statuses` 供前端下拉。

`item_key` 必须是配置词表里的 `domain.aspect`（默认见 `MemoryConfig.item_keys`，可用 `SLEUTH_MEMORY_ITEM_KEYS` 覆盖）。`POST` / `memory_write` 只传目录键；近义（余弦 >= `memory.merge_score`，默认 0.85，env `SLEUTH_MEMORY_MERGE_SCORE`）覆盖该实例，异义新开 `domain.aspect.facet`。跨层近义复用完整键由 `memory.merge_across_scopes`（`SLEUTH_MEMORY_MERGE_ACROSS_SCOPES`）控制。`GET /v1/memory` 同时返回 `item_key_domains` 与 `item_keys` 供前端下拉。

`POST /v1/memory`

```json
{
  "item_key": "output.language",
  "title_text": "回复语言",
  "body_text": "默认用中文回复",
  "scenario_code": "general",
  "mem_kind": "preference"
}
```

默认 `scope_kind=user`、`scope_id` 为当前 `X-User-Id`。写 role/org 层需 `X-Admin-Token`。

`PATCH /v1/memory/{memory_id}` 的 `{memory_id}` 是该行主键 `id`（列表/POST 响应里的 `mem_...`），可改 `title_text` / `body_text`（会重算 embedding，不按近义再分面）。已 `ingested` 的条目若改正文，`kb_status` 自动变为 `stale`（`kb_ref` 保留，便于回写知识库）。只改收纳状态时 PATCH `{ "kb_status": "nominated" }` 或 `{ "kb_status": "ingested", "kb_ref": "kb-doc-id" }`，不重算向量。`memory_write` 工具不能改这些字段。召回注入不受 `kb_status` 影响。`DELETE` 将 `row_status` 置为归档。

### 4.12 目录与授权（Admin）

`GET` / `PUT /v1/directory/users/{user_id}` 维护一人一岗一机构（`role_id` / `org_id`）。换岗改这两列即可。

`GET` / `PUT /v1/directory/grants` 以岗位为主授权 agent/skill。Body 可为单条或 `{ "grants": [ ... ] }`：

```json
{
  "scope_kind": "role",
  "scope_id": "aml_analyst",
  "resource_kind": "agent",
  "resource_id": "dd_reply",
  "grant_effect": "allow"
}
```

总行超管示例（`resource_id` 为通配符，覆盖后续新 agent/skill；不要对整个机构开通配）：

```json
{
  "grants": [
    {"scope_kind": "role", "scope_id": "hq_admin", "resource_kind": "agent", "resource_id": "*", "grant_effect": "allow"},
    {"scope_kind": "role", "scope_id": "hq_admin", "resource_kind": "skill", "resource_id": "*", "grant_effect": "allow"}
  ]
}
```

判定：user deny → user allow → role/org allow → 默认 agent（`SLEUTH_ACL_DEFAULT_AGENT_OPEN`）。用户 deny 只做例外，不要按人预生成全量行。`resource_id` 填配置里的通配符（默认 `*`）表示该 kind 下全部，含后续新增；不要对整个总行机构开通配，否则该机构下全员都会变成超管。有通配授权仍须切到对应 agent 才能调其 MCP 工具，`yolo` ≠ 授权。表需手工创建，代码不执行 `CREATE TABLE`（DDL 与插入示例见 [`ddl_memory_mysql.sql`](ddl_memory_mysql.sql)）。

---

## 5. 前端推荐调用流

```mermaid
sequenceDiagram
  participant FE as Frontend
  participant API as Sleuth_HTTP
  FE->>API: GET /v1/models
  FE->>API: GET /v1/agents
  FE->>API: GET /v1/skills
  FE->>API: POST /v1/sessions (X-User-Id agent model skills)
  API-->>FE: id equals sess_xxx
  FE->>API: GET /v1/sessions (列表页)
  API-->>FE: id title preview time_updated_local
  FE->>API: GET /v1/sessions/{id} (打开会话)
  API-->>FE: messages[]
  FE->>API: GET /v1/sessions/{id}/trace
  API-->>FE: records ledger
  FE->>API: POST /v1/sessions/{id}/messages/stream
  Note over FE,API: SSE text tool_* stop done 均含 session_id
  API-->>FE: done.text title usage
```

1. 登录后固定 `X-User-Id`。
2. 拉 `GET /v1/models`、`GET /v1/agents`、`GET /v1/skills` 做选择器。Skill 选择器仅在当前 agent 等于 `GET /v1/agents` 的 `default` 时启用。
3. 新对话：`POST /v1/sessions`，body 带 `agent` / `model` / `skills`（未选传 `[]`；也可继续传 `skill`）→ 存返回的 **`id`**。
4. 历史列表：`GET /v1/sessions`；详情：`GET /v1/sessions/{id}`。打开会话后可 `GET /v1/sessions/{id}/trace` 画轮次台账（时间条：`started_at` + `duration_ms`；TTFT：`first_token_at - started_at`）。
5. 发消息（推荐流式）：`POST .../messages/stream`，**每轮仍带**当前 `agent` / `model` / `skills`；拼 `text.delta`，以 `done.text` 对齐。台账行用 `step` / `tool_*` / `stop` 追加；进行中不要虚构 duration。
6. 切到专用 agent 时禁用 skill 并传 `skills: []`（及 `skill: ""`）。MCP 晚启动会自动重试；也可 `POST /v1/mcp/reload` 立即重连。

---

## 6. curl 冒烟示例

```powershell
$base = "http://127.0.0.1:8787"
$h = @{ "X-User-Id" = "alice"; "Content-Type" = "application/json" }

# 探活
Invoke-RestMethod "$base/health"

# 创建
$s = Invoke-RestMethod -Method POST "$base/v1/sessions" -Headers $h -Body '{"agent":"build","model":"qwen-max","skill":"","yolo":true}'
$sid = $s.id

# 发消息
$body = @{ prompt = "用一句话介绍你自己"; agent = "build"; model = "qwen-max"; skill = "" } | ConvertTo-Json
Invoke-RestMethod -Method POST "$base/v1/sessions/$sid/messages" -Headers $h -Body $body

# 列表
Invoke-RestMethod "$base/v1/sessions?limit=10" -Headers @{ "X-User-Id" = "alice" }

# 详情
Invoke-RestMethod "$base/v1/sessions/$sid" -Headers @{ "X-User-Id" = "alice" }

# 执行台账
Invoke-RestMethod "$base/v1/sessions/$sid/trace" -Headers @{ "X-User-Id" = "alice" }
```

---

## 7. 与 CLI / MCP 的关系（前端一般不直接调）

| 能力 | HTTP | 说明 |
|------|------|------|
| 会话 CRUD + 对话 | ✅ 本文档 | 前端主路径 |
| MCP 工具（如尽调 `ddcheck_*`） | ❌ 无独立 HTTP | 由 Agent 在 `POST .../messages` 或 `.../messages/stream` 内部调用远程 MCP |
| CLI `/sessions` | — | 与列表接口同源逻辑（`session_browse`） |

若前端需要「尽调检查原始 JSON 结果」，应：让用户/产品在对话里触发 `dd_check`，或后续单独加业务 HTTP 代理；当前 Sleuth HTTP **不**透出 MCP 工具直调。

---

## 8. 版本说明

文档与仓库当前 `sleuth.server` 实现对齐。新增路由时请同步更新本文件与 README 路由表。
