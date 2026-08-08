"""编排门面：对外仍叫 Orchestrator，内部一律走 LangGraph runner。"""
from __future__ import annotations

from typing import Optional

from .config import Settings, get_settings
from .graph.runner import invoke_batch, invoke_check
from .models import BatchCheckRequest, CheckRequest, CheckResult


class Orchestrator:
    """兼容旧调用：check_one / check_batch → graph.runner。"""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        attachment_pipeline=None,  # 历史注入点，图内自行建 pipeline，可忽略
    ):
        self.settings = settings or get_settings()
        self._attachment_pipeline = attachment_pipeline

    def check_one(self, req: CheckRequest) -> CheckResult:
        """单份同步检查（无 HITL 暂停）。"""
        return invoke_check(req, self.settings)

    def check_batch(self, req: BatchCheckRequest) -> dict:
        """批量同步检查。"""
        return invoke_batch(req, self.settings)
