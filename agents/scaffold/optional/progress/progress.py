"""把流水线阶段推给 Sleuth（MCP progress → SSE progress）。

没有单独 env 开关。长步骤在 pipeline 里调 progress_fn("ocr")，不调则前端只有 tool_start。
本文件一般不用改。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional


def report_progress(
    ctx: Any,
    stage: str,
    *,
    detail: str = "",
    current: float = 0,
    total: float = 1,
) -> None:
    """向当前 MCP context 报阶段。ctx 为空则静默。"""
    if ctx is None:
        return
    message = f"{stage}: {detail}" if detail else str(stage)
    fn = getattr(ctx, "report_progress", None) or getattr(ctx, "reportProgress", None)
    if callable(fn):
        try:
            maybe = fn(current, total, message)
        except TypeError:
            try:
                maybe = fn(progress=current, total=total, message=message)
            except Exception:
                maybe = None
        except Exception:
            maybe = None
        if inspect.isawaitable(maybe):
            try:
                import asyncio

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(maybe)
                else:
                    loop.run_until_complete(maybe)
            except Exception:
                pass
        return
    info = getattr(ctx, "info", None)
    if callable(info):
        try:
            info(message)
        except Exception:
            pass


def current_context() -> Any:
    """取 FastMCP 当前请求上下文；不在工具调用里则为 None。"""
    try:
        from fastmcp.server.dependencies import get_context

        return get_context()
    except Exception:
        try:
            from mcp.server.fastmcp import context as _ctxmod

            getter = getattr(_ctxmod, "get_context", None)
            if callable(getter):
                return getter()
        except Exception:
            return None
    return None


def bind_current() -> Optional[Callable[..., None]]:
    """mcp_server 里调用，得到可传入 pipeline 的 progress_fn。"""
    return bind_progress(current_context())


def bind_progress(ctx: Any) -> Optional[Callable[..., None]]:
    """把 ctx 绑成 progress_fn(stage, **kwargs)。"""
    if ctx is None:
        return None

    def _fn(stage: str, **kwargs: Any) -> None:
        report_progress(ctx, stage, **kwargs)

    return _fn
