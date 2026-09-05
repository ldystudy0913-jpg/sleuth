"""Report pipeline stages to Sleuth via MCP progress notifications."""
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
    return bind_progress(current_context())


def bind_progress(ctx: Any) -> Optional[Callable[..., None]]:
    if ctx is None:
        return None

    def _fn(stage: str, **kwargs: Any) -> None:
        report_progress(ctx, stage, **kwargs)

    return _fn
