"""Business logic. MCP handlers call this; do not put side effects in mcp_server.py.

TODO: replace `ping` with your real workflow (API calls, scoring, generation, …).
Keep secrets in environment variables (__ENV_PREFIX___*), never in Skill / Card text.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .attachments import summarize_refs


def ping(message: str, attachment_refs: Optional[List[dict]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "ok": True,
        "echo": message,
        "sources": [],
    }
    if attachment_refs is not None:
        body.update(summarize_refs(attachment_refs))
    return body
