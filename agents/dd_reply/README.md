# dd_reply — 尽调答复框架生成助手

独立 Agent + Skill + MCP 工具面，**不修改** Sleuth 源码。针对对公开户尽调：按风险点编码检索知识（生产走远程知识库 POST），结合 10 个 KYC 字段与附件，生成四段式答复框架（预分析 / 答复正文 / 待核实清单 / 结论判定指引）。

## 安装

```bash
cd agents/dd_reply
py -3.12 -m pip install -e ".[mcp]"
```

可选生产 COS：同时安装 `dd-analyst-capability[all]`，并配置 `.env` 中 `DD_REPLY_MYSQL_*` / `DD_REPLY_COS_*` / `DD_REPLY_ECS_EMODE_B_KEY`。

## 启动 MCP

```bash
cp .env.example .env   # 填写内网 KB URL/Token 与 LLM
py -3.12 -m dd_reply.mcp_server
# 默认 http://127.0.0.1:8792/mcp
```

## 挂到 Sleuth

```env
SLEUTH_MCP_SERVERS={"ddreply":{"type":"remote","url":"http://127.0.0.1:8792/mcp","agent":true}}
```

```bash
py -3.12 -m sleuth --agent dd_reply
```

详见 [HOWTO_SLEUTH.md](HOWTO_SLEUTH.md)。

## 知识来源

配置写在 **`agents/dd_reply/.env`**（复制 `.env.example`），内网地址直接填即可。

| 配置 | 说明 |
|------|------|
| `DD_REPLY_KB_API_URL` | 知识库检索 POST 完整 URL（配了则走远程） |
| `DD_REPLY_KB_API_TOKEN` | Header Token（默认 `Authorization: Bearer …`） |
| `DD_REPLY_KB_KNOWLEDGE_ID` / `DD_REPLY_KB_API_EXTRA_BODY` | 可选请求字段（如 knowledgeId） |
| `DD_REPLY_KB_FALLBACK_LOCAL` | 远程失败是否回退本地 `risk_points.json`（默认 `0`） |
| `DD_REPLY_KB_PATH` | 本地目录；**禁用词始终读**其中的 `lexicon.json` |

| 文件 | 说明 |
|------|------|
| `lexicon.json` | 禁用词 hard/soft（本地，推荐） |
| `risk_points.json` | 离线种子；未配 API URL 时用；或 `FALLBACK_LOCAL=1` 时回退 |

流程：对每个风险编码以 `question=<编码>` 检索 → 用 `paragraph` / `splitContents` 等归纳尽调问题、判断要点、材料与制度 → 结合字段与附件生成框架。材料清单用于判断附件是否**充分**，不要求齐套。

## 本地附件测试

`generate_reply_framework` 传入 `local_paths_json='["./sample.txt"]'`；未配置 LLM 时使用确定性 fallback 骨架。

## 测试

```bash
cd agents/dd_reply
py -3.12 -m unittest discover -s tests -v
```
