# dd_check（Sleuth MCP Agent）

独立项目包：尽调报告填写检查。MCP 工具面 + Agent Card + Skill。不修改 sleuth 内核。

主工具 `check_report`：归一化正文/JSON/附件摘录 → 按需检索本包知识库 → LLM 按 `config/rubric.json` 打维度分 → Python 加权总分 → Word 以 `files[].content_base64` 回给 Sleuth 加密进会话邮箱。

## 开发你要改的文件

| 文件 | 做什么 |
|------|--------|
| [`pipeline.py`](dd_check/pipeline.py) | 检查编排 |
| [`config/rubric.json`](config/rubric.json) | 维度、权重、分制、Word 文件名、KB seed |
| [`config/prompts/`](config/prompts/) | 系统/用户提示词 |
| [`.env.example`](.env.example) | `DD_CHECK_*` 密钥与开关 |
| [`agent.md`](agent.md) | 人设、权限 |
| `skills/dd-check-sop/SKILL.md` | SOP |

## 本地启动

```powershell
cd agents\dd_check
py -3.12 -m pip install -e ".[mcp,cos,docx]"
Copy-Item .env.example .env
# 填写 DD_CHECK_LLM_*（留空则用 Sleuth 会话模型）；附件/KB 按需
py -3.12 -m dd_check.mcp_server
```

探活：`GET http://127.0.0.1:8791/health`

接到 Sleuth：见 [HOWTO_SLEUTH.md](HOWTO_SLEUTH.md)。示例：`py -3.12 -m sleuth --agent dd_check`

| 模块 | 开关 | 行为 |
|------|------|------|
| `attachments.py` | `DD_CHECK_ATTACHMENTS=1`（默认已打开） | `check_report` 声明 `attachment_refs_json` |
| `hitl.py` | `DD_CHECK_HITL=1`（默认已打开） | 空材料返回 `need_input`；基座 `question` 暂停 |
| `llm.py` | 可选 `DD_CHECK_LLM_*` | 本包三项配齐用自己的模型；否则用 Sleuth `sleuth_llm_json` |
| `kb.py` | 四项 `DD_CHECK_KB_*` | 检查过程内检索；亦可注册 `kb_search` |
| `output.py` | 本包 COS 配齐才注册 MCP 工具 | Word 走 `content_base64`；Sleuth 加密上传 |
