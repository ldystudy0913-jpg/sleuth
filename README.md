# sleuth

Python 编程 Agent：本机 CLI 与多用户 HTTP 服务。支持远程 MCP 工具、外部 Skill（本地路径 / HTTP URL / S3），会话与用量按用户隔离持久化（SQLite 或 MySQL）。

整体架构与模块职责见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

对接与扩展文档：

| 文档 | 内容 |
|------|------|
| [docs/API.md](docs/API.md) | HTTP / SSE 前端接口 |
| [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md) | MCP Tool / Agent Card 对接规范 |
| [docs/SKILL_INTEGRATION.md](docs/SKILL_INTEGRATION.md) | Skill 接入与开发规范 |
| [docs/AGENT_SCENARIOS.md](docs/AGENT_SCENARIOS.md) | `dd_analyst` / `dd_reply` 内部流程图 |
| [docs/EXTENDING.md](docs/EXTENDING.md) | 扩展选型与改代码清单 |

要求 **Python ≥ 3.10**。Windows 上若默认 `python` 仍是 3.9，请使用 `py -3.12`。

## 快速开始

```powershell
cd C:\Users\15385\myproject\sleuth
py -3.12 -m pip install -e ".[all]"
Copy-Item .env.example .env
# 编辑 .env：至少设置 SLEUTH_MODEL 与 OPENAI_API_KEY（或兼容网关）
py -3.12 -m sleuth
```

```powershell
py -3.12 -m sleuth --yolo "用三句话说明 sleuth/session.py 做什么"
```

## 配置（`.env` 为主）

模板见 [`.env.example`](.env.example)。启动自动加载；已 export 的环境变量优先。

| 变量 | 含义 |
|------|------|
| `SLEUTH_MODEL` | 默认模型：目录 key，或 `provider/model` |
| `SLEUTH_MODELS` | 模型目录；可无 provider 前缀，每项自带 `apiKey`+`baseURL` |
| `SLEUTH_PROVIDERS` | （可选）按 provider id 配凭证；对象目录已自带时可省略 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | 单网关快捷方式 |
| `SLEUTH_USER_ID` | 用户 ID |
| `SLEUTH_TIMEZONE` | 会话标题/列表时间显示时区（默认 `Asia/Shanghai`） |
| `SLEUTH_STORAGE_BACKEND` | `sqlite`（默认）或 `mysql` |
| `SLEUTH_SKILLS_URLS` | HTTP Skill zip（逗号分隔） |
| `SLEUTH_SKILLS_S3` | S3 Skill（JSON 或 `s3://` 列表） |
| `SLEUTH_SKILLS_REFRESH_SECONDS` | Skill 热更新 TTL |
| `SLEUTH_MCP_SERVERS` | MCP 服务 JSON |
| `SLEUTH_SERVER_*` | HTTP 服务配置 |

复杂嵌套可用 [`sleuth.jsonc.example`](sleuth.jsonc.example) 叠加；同名项以 `.env` 为准。

## Skill（多个）

- 本地：`SLEUTH_SKILLS_PATHS` 或 `.sleuth/skills/`
- HTTP：`SLEUTH_SKILLS_URLS`
- S3（boto3）：`SLEUTH_SKILLS_S3`（多对象 / 前缀 / manifest；单 zip 可含多个 `SKILL.md`）

服务端 / CLI 使用**懒惰 TTL**（默认 `SLEUTH_SKILLS_REFRESH_SECONDS=300`）：距上次刷新满 TTL 后，**下一次** `Session.prompt` 或 `GET /v1/skills` 时自动重扫；**不是**后台定时器。立即生效：CLI 启动加 `--refresh-skills`，或运行中 `POST /v1/skills/reload`。本轮 agent 循环内目录冻结；已写入会话历史的 skill 正文不会被热更改写。

新包上架（COS 前缀 / manifest 等）后若需立刻对所有会话可见，打一次 reload；TTL 可配中长（旧包少改时不必拧得很短）。并发刷新在进程内单飞，缓存落盘为原子解压并按包加文件锁，避免多请求撕同一目录。

## 存储与用量

- SQLite：CLI 默认
- MySQL：`pip install "sleuth[mysql]"`，设 `SLEUTH_STORAGE_BACKEND=mysql`

会话含 `user_id`；每轮写入 `usage_event`；todo 与上下文压缩会落库。

## CLI

```powershell
py -3.12 -m sleuth
py -3.12 -m sleuth --user alice -c
py -3.12 -m sleuth --session sess_xxx
py -3.12 -m sleuth --refresh-skills --yolo "..."
py -3.12 -m sleuth --agent plan "..."
py -3.12 -m sleuth --model openai/gpt-4o-mini
py -3.12 -m sleuth --skill dd-report-check --skill dd-reply-framework
```

交互中浏览 / 切换会话（不必查库）：

```text
>>> /sessions
sessions for user='local' (newest first):
   1. [sess_abc123…] 2026-08-08 18:09:12
      title: New session - 2026-08-08 18:09:12
      preview: 请检查下面这份尽调报告…
>>> /session 1
switched to session sess_abc123...
>>> /session
current session id=...
```

交互中切换模型 / agent / MCP / skills（与 HTTP 目录同语义，粘滞写入会话）：

```text
>>> /model
>>> /model qwen-max
>>> /agent
>>> /agent dd_analyst
>>> /mcp
>>> /mcp reload
>>> /skills
>>> /skill dd-report-check dd-reply-framework
>>> /skill +other-skill
>>> /skill -dd-reply-framework
>>> /skills reload
>>> /usage
>>> /yolo on
```

| CLI 斜杠 | HTTP 能力 |
|----------|-----------|
| `/sessions` `/session` | `GET /v1/sessions`、按 id 续聊 |
| `/model` | `GET /v1/models` + 消息 body `model` |
| `/agent` | `GET /v1/agents` + 消息 body `agent` |
| `/mcp` `/mcp reload` | `GET /v1/mcp` · `POST /v1/mcp/reload` |
| `/skill` `/skills` `/skills reload` | 会话 pin 多个 skill · `GET /v1/skills` · `POST /v1/skills/reload` |
| `/usage` | `GET /v1/users/{id}/usage` |
| `/yolo on\|off` | 消息 body `yolo`（CLI 默认 off，server 默认 on） |

**没有 `deepseek/` 这种 provider 前缀时**（只有 model id，且每套 sk/url 不同），直接在目录里写对象：

```env
SLEUTH_MODEL=deepseek-chat
SLEUTH_MODELS={"deepseek-chat":{"apiKey":"sk-ds","baseURL":"https://api.deepseek.com"},"qwen-max":{"apiKey":"sk-qw","baseURL":"https://dashscope.aliyuncs.com/compatible-mode/v1"}}
```

`/model qwen-max` 会用该条目自己的 key/url，不必再配 `SLEUTH_PROVIDERS`。若有短名需求可写 `"ds":{"model":"deepseek-chat","apiKey":"...","baseURL":"..."}`。

## HTTP 服务

完整前端对接说明（入参/出参/错误码/调用流）：见 [docs/API.md](docs/API.md)。

```powershell
py -3.12 -m sleuth.server
# 请求头 X-User-Id；管理接口 X-Admin-Token
```

| 方法 | 路径 |
|------|------|
| GET | `/health` |
| POST/GET | `/v1/sessions`（创建可选 body `model`；列表含 `preview` / `time_updated_local`） |
| GET | `/v1/sessions/{id}`（含 `model`、messages、计时字段） |
| GET | `/v1/sessions/{id}/trace`（执行台账；见 [docs/API.md](docs/API.md) §4.4.1） |
| POST | `/v1/sessions/{id}/messages`（可选 body `model`，本轮前切换；**同步长耗时**） |
| POST | `/v1/sessions/{id}/messages/stream`（同上 Body；**SSE**，见 [docs/API.md](docs/API.md) §4.5.1） |
| GET | `/v1/models` · `/v1/agents`（选择器目录；agents 含 available） |
| GET | `/v1/mcp` · `POST /v1/mcp/reload`（连接状态 / 热重载） |
| GET | `/v1/users/{user_id}/usage` |
| GET/POST | `/v1/skills` · `/v1/skills/reload` |

## MCP

```bash
SLEUTH_MCP_SERVERS={"docs":{"type":"remote","url":"https://mcp.example.com/mcp"}}
```

工具名：`{server}_{tool}`。不必先起齐 MCP：Sleuth 先启动，未连上的服务按 `SLEUTH_MCP_RETRY_SECONDS`（默认 15）后台重试；下一轮对话自动挂上工具。立即重连用 `/mcp reload`。

## 测试

```powershell
py -3.12 -m unittest tests.test_skills_and_store -v
```

## 产品护栏

默认开启（`SLEUTH_GUARDRAILS=1`）：

- 工具无法读取/改写 **sleuth 包源码**、**系统 prompt**、**`.env` 等密钥**
- 系统提示注入公开 **工具 / Skill** 目录；打听内部实现时应拒绝并只谈该目录
- `--yolo` **不能**绕过路径硬拦截；自研本仓库时设 `SLEUTH_GUARDRAILS=0`

## 扩展开发

完整步骤见 [docs/EXTENDING.md](docs/EXTENDING.md)：选型（Tool / Skill / MCP / Agent / Store）、注册与权限、配置、HTTP、验证与 PR 清单。
