"""业务逻辑。MCP 只做参数拼装，副作用和算法写在这里。

二次开发：把 `ping` 换成真实流程（调上游 API、打分、生成……）。
密钥只放环境变量（__ENV_PREFIX___*），不要写进 Skill / Card。

可选能力不会自动跑：
- 附件：调用方传入 `attachment_refs`，这里用 `summarize_refs` 读 excerpt。
- LLM：在本函数里 `from .llm import chat_completion, settings_with_llm_json`。
- 进度：调用 `progress_fn("阶段名")`，不调则 SSE 只有 tool_start。
HITL / need_input 不在本文件判断，写在 mcp_server 的包装函数里。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .attachments import summarize_refs


def ping(
    message: str,
    attachment_refs: Optional[List[dict]] = None,
    progress_fn=None,
) -> Dict[str, Any]:
    """演示回显。请改成你的主业务流程并在 mcp_server 里换工具名。

    attachment_refs: Sleuth 注入的会话摘录列表；未开 ATTACHMENTS 时为 None。
    progress_fn: 可选，签名 progress_fn(stage: str)，把阶段名推给基座。
    """
    if callable(progress_fn):
        try:
            progress_fn("ping")
        except Exception:
            pass
    body: Dict[str, Any] = {
        "ok": True,
        "echo": message,
        "sources": [],
    }
    if attachment_refs is not None:
        body.update(summarize_refs(attachment_refs))
    return body
