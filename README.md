# sleuth

Python 编程 Agent：本机 CLI 与多用户 HTTP 服务。支持远程 MCP 工具、外部 Skill（本地路径 / HTTP URL / S3），会话与用量按用户隔离持久化（SQLite 或 MySQL）。

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
| `SLEUTH_MODEL` | 模型，如 `openai/gpt-4o` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI 兼容接口 |
| `SLEUTH_USER_ID` | 用户 ID |
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

服务端按 TTL + ETag 条件刷新；`POST /v1/skills/reload` 可立即重载。CLI：`--refresh-skills`。

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
```

## HTTP 服务

```powershell
py -3.12 -m sleuth.server
# 请求头 X-User-Id；管理接口 X-Admin-Token
```

| 方法 | 路径 |
|------|------|
| GET | `/health` |
| POST/GET | `/v1/sessions` |
| GET | `/v1/sessions/{id}` |
| POST | `/v1/sessions/{id}/messages` |
| GET | `/v1/users/{user_id}/usage` |
| GET/POST | `/v1/skills` · `/v1/skills/reload` |

## MCP

```bash
SLEUTH_MCP_SERVERS={"docs":{"type":"remote","url":"https://mcp.example.com/mcp"}}
```

工具名：`{server}_{tool}`。

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
