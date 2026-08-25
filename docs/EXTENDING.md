# sleuth 扩展开发约定

本文说明如何把能力接进 sleuth：**先选型，再按步骤改代码/配置，最后本地验证**。  
共享装配入口：[`sleuth/app.py`](../sleuth/app.py)（CLI 与 HTTP 都走这里）。会话循环：[`sleuth/session.py`](../sleuth/session.py)。

专题文档（推荐直接阅读）：

- [MCP 对接（Tool / Agent Card）](MCP_INTEGRATION.md)
- [Skill 接入与开发规范](SKILL_INTEGRATION.md)
- [Agent 场景内部流程（dd_analyst / dd_reply）](AGENT_SCENARIOS.md)
- [HTTP API](API.md)

---

## 1. 先选型（不要一上来写代码）

| 你想做的事 | 选什么 | 不要选 |
|------------|--------|--------|
| 让模型**执行**一段确定性逻辑（调 API、改文件、跑脚本） | **内置 Tool** 或 **MCP Tool** | Skill（Skill 不会执行） |
| 能力在**外部服务**里，或希望独立部署/多语言 | **MCP**（`SLEUTH_MCP_SERVERS`） | 除非必须本地强集成，否则别写 Python Tool |
| 给模型一段**可复用流程/规范**（怎么排查、怎么发版） | **Skill**（`SKILL.md`） | Tool（除非还要执行） |
| 换一套权限/提示词/工具可见性 | **Agent**（`build` / `plan` / 自定义） | 硬改全局 permission |
| 会话、用量、todo 换库或加字段 | **Store** | 在 Tool 里自己写文件当库 |
| 新开关、密钥、路径 | **Config / `.env`** | 在业务代码里写死 |
| 对外暴露 HTTP | **`sleuth/server/`**，内部仍调 `build_session` | 复制一套 CLI 逻辑 |

经验法则：

1. **能做成 MCP 的优先 MCP**（零改内核、可热插拔）。
2. **流程知识做成 Skill**；**副作用做成 Tool**。
3. **CLI 与 HTTP 共用的能力**改 `app.py` / `session` / `tools` / `storage`，不要只改 `cli.py`。

---

## 2. 仓库地图（扩展时会碰到的文件）

```
sleuth/
  app.py              # 装配：registry + store + permission + session
  session.py          # Agent 循环：流式事件、调 tool、落库
  config.py           # .env + JSONC → Config
  agent.py            # 内置 agent 权限基线
  permission.py       # allow / deny / ask
  cli.py              # 终端 UI（RichRenderer）
  tools/
    base.py           # Tool / ToolResult / ToolContext
    registry.py       # _builtins() + register
    *.py              # 各内置工具
  skill/              # Skill 发现 / 缓存 / 热更新
  mcp/                # MCP 连接 + bridge 成 Tool
  storage/            # Store 协议 + sqlite / mysql
  provider/           # 模型适配
  server/app.py       # HTTP 路由
tests/
  test_skills_and_store.py
.env.example          # 新配置项必须同步
```

启动链路（扩展时心里要有这条路）：

```
CLI / HTTP
  → load(.env) → build_session()
    → create_store()
    → ruleset_for(agent) + config.permission
    → ToolRegistry(_builtins) + MCP bridge_tools
    → ensure_skills_fresh()
    → Session.prompt() 循环：模型 → tool.execute → 再喂回模型
```

---

## 3. 新增内置 Tool（完整步骤）

适用：能力必须跑在本进程、需要 `workdir` / 权限门禁、希望写进默认工具集。

### 步骤

1. **起名**  
   - 小写、短、稳定（会进模型 tool schema 与 permission key）。  
   - 勿与 MCP 合格名冲突（MCP 形如 `{server}_{tool}`）。

2. **实现文件** `sleuth/tools/<name>.py`  
   - `params`：Pydantic `BaseModel`（字段加 `Field(description=...)`，模型靠这个选型）。  
   - `description`：写清何时用、参数含义、副作用。  
   - `execute(args, ctx)`：  
     - 需要授权时：`ctx.ask("<tool_name>", [pattern], always=[...])`  
     - 成功：`ToolResult.success(title, output, **metadata)`  
     - 失败：`ToolResult.error(title, message)`（不要抛未捕获异常给模型）  
   - 参考：[`webfetch.py`](../sleuth/tools/webfetch.py)、[`bash.py`](../sleuth/tools/bash.py)。

3. **注册**  
   - 在 [`registry.py`](../sleuth/tools/registry.py)：`from .xxx import XxxTool`，加入 `_builtins()`。  
   - 动态注册也可：`registry.register(XxxTool())`（一般在 `build_registry` 里做）。

4. **权限默认值**  
   - 在 [`permission.py`](../sleuth/permission.py) 的 `build_rules()` / `plan_rules()`（以及 `agent.py` 里 explore/general 如需）补一条：  
     - 只读安全 → `"allow"`  
     - 有副作用 / 外网 → `"ask"`  
     - plan 模式不应写文件 → `"deny"`  
   - `deny` + `pattern="*"` 会让工具对模型**不可见**。

5. **（可选）配置**  
   - 超时、开关等：在 `Config` + `_apply_env` + `.env.example` 加项（见第 8 节）。

6. **验证**  
   ```powershell
   py -3.12 -c "from sleuth.tools.registry import ToolRegistry; print(ToolRegistry().names())"
   py -3.12 -m sleuth --yolo "调用 <tool> 做一次最小演示"
   ```
   - `--yolo` 会 `allow_all`，适合冒烟；正式路径再测 `ask`/`deny`。

### 最小骨架

```python
# sleuth/tools/ping_api.py
from pydantic import BaseModel, Field
from .base import ToolContext, ToolResult

class PingParams(BaseModel):
    url: str = Field(description="HTTPS endpoint to ping")

class PingApiTool:
    name = "ping_api"
    description = "Ping an HTTPS URL and return status code + body snippet."
    params = PingParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = PingParams(**args)
        try:
            ctx.ask("ping_api", [p.url], ["*"])
        except Exception as exc:
            return ToolResult.error("ping_api", f"permission denied: {exc}")
        # ... do work ...
        return ToolResult.success("ping_api", "ok", status=200)
```

### 自检清单

- [ ] 已进 `_builtins()`  
- [ ] `build` / `plan`（及用到的 agent）有默认 permission  
- [ ] `execute` 内对危险操作调用了 `ctx.ask`  
- [ ] 错误走 `ToolResult.error`  
- [ ] description / Field description 足够让模型选对工具  

---

## 4. 通过 MCP 扩展 Tool（推荐的外部扩展路径）

适用：工具已在别的服务里，或不想改 sleuth 源码。

### 步骤

1. 准备 MCP server（remote HTTP 或 stdio command）。  
2. 在 `.env` 配置：
   ```bash
   SLEUTH_MCP_SERVERS={"docs":{"type":"remote","url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer xxx"}}}
   ```
   或用 `sleuth.jsonc` 的 `mcp` 块做复杂结构（仍建议密钥放 `.env`）。  
3. 重启 CLI / server；启动时 [`app.build_registry`](../sleuth/app.py) 会 `bridge_tools`。  
4. 确认工具名：`{server}_{原工具名}`。  
5. 权限：默认走 ask；可在 config `permission` 里按工具名设 `allow`/`deny`。  
6. Skill 若声明依赖 MCP，frontmatter 写 `mcp: [docs]`（见第 5 节）。

### 自检

```powershell
py -3.12 -m sleuth --yolo "列出你可用的工具名，看是否包含 docs_..."
```

出问题先看启动时 `mcp init failed` / `mcp_manager.errors`（CLI 会 `on_error`）。

相关代码：[`sleuth/mcp/manager.py`](../sleuth/mcp/manager.py)、[`bridge.py`](../sleuth/mcp/bridge.py)。

### MCP Agent Card（opt-in：用 MCP 注册 Agent）

默认 **兼容旧配置**：`SLEUTH_MCP_SERVERS` 不写 `"agent":true` 时，只桥接 Tools，与改前一致。

需要「人设 + 建议权限 + Skill」也从 MCP 下发时：

1. MCP server 实现工具 **`get_agent_card`**（无参，返回 JSON：`name` / `prompt` / `permission` / `skills`）。  
2. Sleuth 配置：
   ```bash
   SLEUTH_MCP_SERVERS={"ddcheck":{"type":"remote","url":"http://127.0.0.1:8791/mcp","agent":true}}
   ```
3. 启动后可用 `sleuth --agent <card.name>`；本地 `.opencode/agent` / `SLEUTH_SKILLS_PATHS` 仍可用且**优先于** Card。  
4. 安全：Card 里对 `bash`/`edit`/`write`/`task` 的 `allow` 默认降为 `ask`（`SLEUTH_MCP_AGENT_TRUST_PERMISSIONS=1` 可关闭消毒）。  
5. 拉 Card 失败只记 error，**不阻断**工具注册。

约定与解析：[`sleuth/mcp/agent_card.py`](../sleuth/mcp/agent_card.py)。样例 Agent：[`agents/dd_analyst`](../agents/dd_analyst)。

---

## 5. 新增 / 更新 Skill

Skill = 带 frontmatter 的说明包；模型通过内置 `skill` 工具按名加载正文，**不会自动执行脚本**（脚本需 Skill 正文里指导模型去 `bash`/`read`）。

### 步骤（本地开发最快）

1. 建目录：
   ```
   .sleuth/skills/my-skill/SKILL.md
   ```
   也可放全局：`~/.config/sleuth/skills/...`，或配置 `SLEUTH_SKILLS_PATHS`。

2. 写 `SKILL.md`：
   ```markdown
   ---
   name: my-skill
   description: 何时使用：一句话给模型选型
   mcp: []          # 可选：依赖的 MCP server 名
   tools: []        # 可选：依赖的 tool 名
   ---

   # 步骤
   1. ...
   2. ...
   ```

3. 加载验证：
   ```powershell
   py -3.12 -m sleuth --refresh-skills --yolo "用 skill 工具加载 my-skill 并总结要点"
   ```
   HTTP：`GET /v1/skills`（会走懒惰 TTL），强制刷新 `POST /v1/skills/reload`（需 admin token 时带头）。

### 热加载语义

| 方式 | 行为 |
|------|------|
| `SLEUTH_SKILLS_REFRESH_SECONDS`（默认 300） | **懒惰 TTL**：满间隔后，下一次 `Session.prompt()` / `GET /v1/skills` 自动 `discover`。无后台线程。旧包少改、新包多时可配中长 TTL；新包要立刻可见时上架后打一次 reload，不必把 TTL 拧到极短。 |
| 手动立即 | 启动 CLI：`--refresh-skills`；已运行的 HTTP：`POST /v1/skills/reload` |
| 本轮内 | 目录在 `prompt()` 开头刷新后冻结；`skill` 工具写入历史的正文不会被中途改写 |
| 远程源 | URL/S3 用 ETag / LastModified；未变则跳过重下 |
| 并发安全 | 进程内刷新单飞；落盘按 cache_key 文件锁 + 临时目录原子切换，避免多 worker 撕同一缓存目录 |

实现：[`sleuth/skill/__init__.py`](../sleuth/skill/__init__.py)；触发点：[`session.prompt`](../sleuth/session.py)。

### 远程分发

| 来源 | 配置 | 注意 |
|------|------|------|
| HTTP zip | `SLEUTH_SKILLS_URLS` | zip 内可含多个 `**/SKILL.md` |
| S3 | `SLEUTH_SKILLS_S3` | 单对象 / 前缀 / manifest；需 boto3 + AWS 凭证 |

### 自检清单

- [ ] frontmatter 有稳定 `name` + 可选型的 `description`  
- [ ] 相对路径以 Skill 目录为根（加载后会注入 Base directory）  
- [ ] 声明的 `mcp` / `tools` 在运行环境真实存在（否则只有 warning）  
- [ ] 改远程包后做过 reload，而不是只改源站以为进程已更新  

---

## 6. 新增 / 调整 Agent

Agent = **权限基线 +（可选）提示词/模型/步数**，不是另一套循环。

### 内置基线

见 [`agent.py`](../sleuth/agent.py)：`build` / `plan` / `general` / `explore`。

### 自定义 Agent（配置，不改代码）

在 `sleuth.jsonc`（或项目约定的 agent markdown 叠加，见 `config.py`）增加：

```jsonc
{
  "agent": {
    "reviewer": {
      "title": "只读评审",
      "description": "只读评审代码，不改文件",
      "mode": "primary",
      "permission": {
        "edit": "deny",
        "write": "deny",
        "bash": "ask",
        "read": "allow"
      }
    }
  }
}
```

使用：`py -3.12 -m sleuth --agent reviewer "..."`。

### 要改代码时

1. 在 `agent.BUILTIN` 加 ruleset，或扩展 `ruleset_for`。  
2. 若新 agent 需要不同工具集：用 permission `deny *` 隐藏工具，而不是从 registry 删掉（除非全局不要）。  
3. 同步 README「CLI `--agent`」说明。

装配顺序（后写覆盖前写）：`ruleset_for(agent)` → `agent_cfg.permission` → `config.permission`；`--yolo` 则全部 allow。

---

## 7. 扩展存储（Store）

协议：[`storage/base.py`](../sleuth/storage/base.py)（`SessionRecord` / `Message` / todo / `UsageEvent`）。

`message` / `part` 的主键由 [`util/ids.py`](../sleuth/util/ids.py) 生成随机 ID（`msg_` / `part_` + hex），保证跨进程重启与多 worker 全局唯一。**不要**改回进程内单调计数器，否则 MySQL 会报 `Duplicate entry 'part_N' for key 'part.PRIMARY'`。

### 新增后端步骤

1. 实现 `Store` 全部方法（对照 [`sqlite.py`](../sleuth/storage/sqlite.py)）。  
2. 在 [`factory.py`](../sleuth/storage/factory.py) `create_store` 增加 `kind` 分支。  
3. `StorageConfig` + `_apply_env` + `.env.example` 增加连接项。  
4. 可选 extras：`pyproject.toml` 的 optional dependency。  
5. 测试：在 `tests/test_skills_and_store.py` 加最小 create/list/usage 用例。  
6. 多用户：所有读写带 `user_id`；HTTP 用 `X-User-Id`。

### 改表 / 加字段

- SQLite：迁移要兼容旧库（先探测列/表，再 `ALTER`；索引建在迁移之后）。  
- MySQL：同样提供幂等迁移。  
- 会话循环里若写入新字段，同步改 `Session` 落库路径与 HTTP 序列化。

---

## 7b. 产品护栏（披露边界）

默认 `Config.guardrails=True`（`SLEUTH_GUARDRAILS`）。实现：[`sleuth/guardrails.py`](../sleuth/guardrails.py)。

| 层 | 行为 |
|----|------|
| 硬拦截 | `read` / `grep` / `glob` / `edit` / `write` / `bash` 拒绝包根与密钥路径；`--yolo` 无效 |
| 软策略 | `assemble()` 注入 disclosure policy + Public tools + Available skills |

自研 sleuth 源码时：`.env` 设 `SLEUTH_GUARDRAILS=0`。扩展新文件工具时务必调用 `deny_if_protected`。

## 7c. 输出脱敏（PII）

默认 `Config.output_desensitize=True`（`SLEUTH_OUTPUT_DESENSITIZE`）。实现：[`sleuth/privacy.py`](../sleuth/privacy.py)。

在 `Session` 主循环中，助手文本 / 推理 / 工具结果 / 错误信息在**落库与渲染前**脱敏；`GET /v1/sessions/{id}` 再 scrub 一次（兼容历史明文）。

| 规则 | 行为（示意） |
|------|----------------|
| 身份证 | 保留前 3 后 2 |
| 手机 | `138****5678` |
| 银行卡 | 保留后 4 位 |
| 密码标签 | `密码：***` |
| 住址 | 仅当带「家庭住址/住址/地址：」等标签且值较长时掩码为 `***` |

关闭：`.env` 设 `SLEUTH_OUTPUT_DESENSITIZE=0`（仅排障）。无标签的自由地址不做 NER 猜测，避免误伤经营范围等字段。

## 8. 新增配置项

原则：**`.env` 优先**；JSONC 只放嵌套结构。

### 步骤

1. 在 [`config.py`](../sleuth/config.py) 对应 dataclass 加字段（`Config` / `SkillsConfig` / …）。  
2. 在 `_apply_env` 读 `SLEUTH_*`（类型转换、空串忽略）。  
3. 若 JSONC 也要支持：在 `Config.merge` 解析同名键。  
4. 更新 [`.env.example`](../.env.example) 注释示例。  
5. README 配置表补一行（用户可见项）。  
6. 业务代码通过 `config.xxx` 读取，不要散落 `os.environ.get`（CLI 纯展示类开关除外）。

---

## 9. 扩展 HTTP API

文件：[`sleuth/server/app.py`](../sleuth/server/app.py)。

### 步骤

1. 写 handler：从 header 取 `X-User-Id`；管理操作校验 `X-Admin-Token`。  
2. **复用** `build_session` / `create_store` / skill API，禁止复制一套 agent 循环。  
3. `routes = [ Route(...), ... ]` 注册。  
4. README 路由表更新。  
5. 冒烟：
   ```powershell
   py -3.12 -m sleuth.server
   # 另开终端
   curl http://127.0.0.1:8787/health
   ```

现有路由：`/v1/sessions`、`.../trace`、`.../files/uploads|complete`、`.../files`、`.../messages`、`/v1/users/{id}/usage`、`/v1/skills`、`/v1/skills/reload`。

---

## 9b. MCP 会话附件约定

会话文件走 COS 邮箱；基座解密后抽出文本（PDF/xlsx/docx/图片），摘录注入默认 agent 与 MCP refs：

- **入参**：若工具 JSON Schema 含 `attachment_refs_json`，`McpBridgeTool` 在调用前注入 `[{file_id, filename, mime, size, object_key, url, excerpt, truncated, encrypted, excerpt_status}]`（`url` 为短时 HTTPS GET，对象仍是密文）。专用 agent 应优先用 `excerpt`，不要 GET 密文当文本。
- **出参**：工具返回 JSON 若含 `files[]`（`filename` / `mime` / `object_key` 或 `https` `url`），基座登记为会话 `role: assistant` 文件，并出现在同步响应 / SSE `done.files`。禁止 data-URL。

默认 agent `build` 另有内置 `kb_lookup`（`SLEUTH_KB_API_URL` + 登录 Cookie `ragToken`）、`read_session_file`（读摘录）与 `save_output_file`（把生成文本写入同一 COS 邮箱）。

---

## 10. 扩展 Provider / 模型

1. 实现 [`provider/base.py`](../sleuth/provider/base.py) 约定的流式接口。  
2. 在 [`provider/factory.py`](../sleuth/provider/factory.py) 按 `provider/model` 解析并实例化。  
3. 用 `SLEUTH_MODEL=provider/model-id` 验证流式 `text` / `reasoning` / `tool_call` 事件都能到 `Session`。  
4. CLI 折叠思考依赖 `on_reasoning`；新 provider 若支持 thinking，务必发 reasoning 事件（字段名兼容 `reasoning` / `reasoning_content` / `thinking`）。  
5. **会话内切模型**：CLI `/model` 或 `/model <alias|provider/model>`（见 `Session.set_model`）；HTTP 在 `POST /v1/sessions` 与 `.../messages` 传 `model`。多套 sk/url 用 `SLEUTH_MODELS` 对象目录（每项可带 `apiKey`/`baseURL`），默认模型仍设 `SLEUTH_MODEL`。

---

## 11. 推荐开发工作流（每次扩展都走一遍）

```text
1. 选型（§1）并列出将改动的文件
2. 实现最小可用（先 happy path）
3. 接 permission / config / 注册点
4. 单元测试或 -c 冒烟
5. 交互路径再测一遍（无 --yolo）
6. 同步 .env.example + README（若用户可见）
7. 跑：py -3.12 -m unittest tests.test_skills_and_store -v
```

常用命令：

```powershell
# 安装（开发模式）
py -3.12 -m pip install -e ".[all]"

# 单元测试
py -3.12 -m unittest tests.test_skills_and_store -v

# CLI 冒烟（自动批准工具）
py -3.12 -m sleuth --yolo "..."

# 强制刷新 Skill
py -3.12 -m sleuth --refresh-skills --yolo "加载 xxx skill"

# 指定用户 / 续聊
py -3.12 -m sleuth --user alice -c
```

---

## 12. 端到端示例：加一个「查内部工单」能力

假设工单在外部 HTTP 服务。

| 阶段 | 动作 |
|------|------|
| 选型 | 外部 API → **MCP** 或内置 Tool；流程话术 → **Skill** |
| A. MCP | 独立 MCP 暴露 `get_ticket`；`.env` 配 `SLEUTH_MCP_SERVERS`；权限 `docs_get_ticket: allow` |
| B. 内置 | 写 `TicketTool` → `_builtins` → `build_rules` 里 `ask` → 单测/冒烟 |
| C. Skill | `SKILL.md` 教模型：先 `skill` 加载，再调 ticket 工具，输出固定模板 |
| 验证 | `--refresh-skills` + `--yolo` 跑一条真实工单号 |
| 文档 | `.env.example` 补 MCP 示例；README 一句说明 |

---

## 13. PR / 合并前检查

- [ ] 选型合理（Tool / Skill / MCP / Agent / Store 没混用）  
- [ ] Tool 已注册；危险操作有 `ctx.ask`；agent 基线有默认 permission  
- [ ] Skill 有 `name` + `description`；远程更新可 reload  
- [ ] 新配置有 `_apply_env` + `.env.example`（用户可见再写 README）  
- [ ] CLI/HTTP 共享逻辑放在 `app` / 内核，不单改一侧  
- [ ] 无密钥、无真实 `.env` 入库  
- [ ] `py -3.12 -m unittest tests.test_skills_and_store -v` 通过  
- [ ] 至少一条人工冒烟命令写在 PR 描述里  

---

## 14. 常见坑

| 现象 | 原因 | 处理 |
|------|------|------|
| 模型看不到新工具 | 未注册，或 `deny *` 隐藏 | 查 `_builtins` / permission |
| 一直 ask | 未在 rules 设 allow，且没用 `--yolo` | 配 permission 或开发时 `--yolo` |
| Skill 找不到 | 路径不对 / 缓存未刷新 | `--refresh-skills` 或 reload |
| MCP 工具名为空 | 连接失败被吞 | 看启动 error；查 URL/headers |
| HTTP 有、CLI 无（或相反） | 只改了一侧入口 | 改 `build_session` / `build_registry` |
| SQLite 升级崩 | 索引建在缺列之前 | 先迁移列再建索引 |
| Windows 编码乱码 | 控制台非 UTF-8 | CLI `main` 已 reconfigure；脚本自行设 UTF-8 |
