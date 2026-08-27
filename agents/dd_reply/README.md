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
# 默认 MCP:    http://127.0.0.1:8792/mcp
# 健康探测:    GET http://127.0.0.1:8792/health
```

Docker / 编排探活（进程起来即 200；body 含 KB/LLM 配置诊断）：

```bash
curl -f http://127.0.0.1:8792/health
# HEALTHCHECK CMD curl -f http://127.0.0.1:8792/health || exit 1
```

容器内请把 `DD_REPLY_MCP_HOST=0.0.0.0`，探活地址用容器端口。

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
| `DD_REPLY_KB_API_URL` | **必填**。风险点知识只走该检索 API，连不上或无命中即标缺失，不再读本地 JSON |
| `DD_REPLY_KB_LOGIN_URL` / `DD_REPLY_KB_OPENID` / `DD_REPLY_KB_SERVICEID` | **必填**。登录拿 `ragToken`，检索请求带 `Cookie: ragToken=…` |
| `DD_REPLY_KB_KNOWLEDGE_IDS` | 可选召回库 ID（逗号分隔）；不配则走服务默认配置 |
| `DD_REPLY_KB_SORT_COUNT` | 每个风险编码保留的命中条数（默认 10） |
| `DD_REPLY_KB_PATH` | 本地目录；**仅禁用词**读其中的 `lexicon.json` |

| 文件 | 说明 |
|------|------|
| `lexicon.json` | 禁用词 hard/soft（本地） |

流程：对每个风险编码或名称以 `question=<该项>` 检索（可选 `serviceConfig`）→ 按 `finalResponse` / `comprehended` / `rankScore` 排序后取前 `SORT_COUNT` 条 → 结合字段与附件生成框架。正文里用来源灰色括号标 `file_name` 与知识 URL；文末用虚线与正文分开，再灰色列出 `《文件名》：url`（每文件一行，链接可点，不做脱敏）。材料清单用于判断附件是否**充分**，不要求齐套。

## 本地附件测试

`generate_reply_framework` 传入 `local_paths_json='["./sample.txt"]'`；未配置 LLM 时使用确定性 fallback 骨架。

## 测试

```bash
cd agents/dd_reply
py -3.12 -m unittest discover -s tests -v
```
