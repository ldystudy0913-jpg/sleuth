# 将 dd_reply 挂到 Sleuth

## 1. 启动工具面

```bash
cd agents/dd_reply
py -3.12 -m pip install -e ".[mcp]"
py -3.12 -m dd_reply.mcp_server
```

默认监听 `http://127.0.0.1:8792/mcp`。探活：`GET http://127.0.0.1:8792/health`（HTTP 200）。

## 2. 配置 Sleuth

在 sleuth 工作目录 `.env`（或环境变量）中：

```env
SLEUTH_MCP_SERVERS={"ddreply":{"type":"remote","url":"http://127.0.0.1:8792/mcp","agent":true}}
```

`agent:true` 会调用 `get_agent_card`，自动注册 `dd_reply` 人格与 `dd-reply-framework` 技能。

可选：在 **dd_reply 进程** 的 `agents/dd_reply/.env` 配置知识库（内网 URL 写这里）：

```env
DD_REPLY_KB_API_URL=http://your-intranet-kb/api/search
DD_REPLY_KB_API_TOKEN=your-token
# DD_REPLY_KB_KNOWLEDGE_ID=
# DD_REPLY_KB_TOP_K=8

# 仅禁用词目录（默认包内 kb/lexicon.json）
# DD_REPLY_KB_PATH=D:/kb/dd_reply
```

必须配置 `DD_REPLY_KB_API_URL`：风险点只从知识库 API 取；连不上或无结果会标缺失，不会回退本地 JSON。禁用词始终用本地 `lexicon.json`。

## 3. 运行

```bash
py -3.12 -m sleuth --agent dd_reply
```

示例用户话术：

> 风险点 C001、C003，客户名称××公司，成立时间 2019-01，请生成答复框架。

Agent 应按 Skill 调用 `ddreply_generate_reply_framework`。

## 4. 本地附件

测试时可让工具使用：

```text
local_paths_json=["C:/tmp/interview_notes.txt"]
```

生产 COS 需配置 `DD_REPLY_*` MySQL/COS/SM4，并安装 `dd-analyst-capability` 附件依赖。
