"""Optional cmb log_trace adapter. Do not name this package log_trace."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Optional

_LEVEL_MAP = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "ERROR",
}

_tracer: Any = None
_ready: bool = False
_handler_installed: bool = False


def is_enabled(config: Any = None) -> bool:
    if config is not None:
        lt = getattr(config, "log_trace", None)
        if lt is not None:
            return bool(getattr(lt, "enabled", False))
    raw = os.environ.get("SLEUTH_LOG_TRACE") or os.environ.get("SLEUTH_LOG_TRACE_ENABLED")
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def get_tracer() -> Any:
    return _tracer


def config_file(config: Any = None) -> Optional[str]:
    path = ""
    if config is not None:
        lt = getattr(config, "log_trace", None)
        if lt is not None:
            path = str(getattr(lt, "config_file", "") or "")
    if not path:
        path = (os.environ.get("SLEUTH_LOG_TRACE_CONFIG") or "").strip()
    path = path.strip()
    return path or None


def ignore_paths(config: Any = None) -> List[str]:
    paths: List[str] = []
    if config is not None:
        lt = getattr(config, "log_trace", None)
        if lt is not None:
            raw = getattr(lt, "ignore_paths", None) or []
            paths = [str(p).strip() for p in raw if str(p).strip()]
    if not paths:
        env = os.environ.get("SLEUTH_LOG_TRACE_IGNORE_PATHS") or ""
        paths = [p.strip() for p in env.split(",") if p.strip()]
    return paths or ["/health"]


def _verbose(config: Any = None) -> bool:
    if config is not None:
        lt = getattr(config, "log_trace", None)
        if lt is not None and getattr(lt, "verbose", None) is not None:
            return bool(lt.verbose)
    raw = os.environ.get("SLEUTH_LOG_TRACE_VERBOSE")
    if raw is None or raw == "":
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _async_mode(config: Any = None) -> bool:
    if config is not None:
        lt = getattr(config, "log_trace", None)
        if lt is not None and getattr(lt, "async_mode", None) is not None:
            return bool(lt.async_mode)
    raw = os.environ.get("SLEUTH_LOG_TRACE_ASYNC")
    if raw is None or raw == "":
        return True
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_config_path(path: Optional[str], workdir: Optional[Path] = None) -> Optional[str]:
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return str(p)
    if workdir is not None:
        alt = (workdir / path).resolve()
        if alt.is_file():
            return str(alt)
    cwd = Path.cwd() / path
    if cwd.is_file():
        return str(cwd.resolve())
    return str(p)


def _has_cmb_env() -> bool:
    return all(
        (os.environ.get(k) or "").strip()
        for k in ("CMB_BUSINESSID", "CMB_CAAS_DEPLOYUNITID", "CMB_CAAS_SERVICEUNITID")
    )


def _has_request_context(tracer: Any) -> bool:
    getter = getattr(tracer, "_get_current_trace_id", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return False
    try:
        from log_trace.request_context_manager import get_request_context

        get_request_context("last_callstack")
        return True
    except Exception:
        return False


class _TraceLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        tracer = _tracer
        if tracer is None:
            return
        try:
            msg = self.format(record)
            level = _LEVEL_MAP.get(record.levelno, "INFO")
            stack = ""
            if record.exc_info:
                stack = self.formatException(record.exc_info)
            tracer.log(msg, level, error_stack=stack)
        except Exception:
            pass


def _install_log_handler() -> None:
    global _handler_installed
    if _handler_installed:
        return
    handler = _TraceLogHandler()
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    log = logging.getLogger("sleuth")
    log.addHandler(handler)
    if log.level == logging.NOTSET:
        log.setLevel(logging.INFO)
    _handler_installed = True


def ensure_initialized(config: Any = None, *, workdir: Optional[Path] = None) -> None:
    """Init official LogTrace when enabled. Fail fast if the package or identity is missing."""
    global _tracer, _ready
    if not is_enabled(config):
        return
    if _ready and _tracer is not None:
        return
    try:
        from log_trace.fastapi_log_trace import FastapiLogTrace
        from log_trace import get_tracer as lib_get_tracer
    except ImportError as exc:
        raise RuntimeError(
            "SLEUTH_LOG_TRACE=1 requires the intranet log_trace package "
            "(and fastapi). Install log_trace from the company index."
        ) from exc

    path = _resolve_config_path(config_file(config), workdir)
    if path and not Path(path).is_file():
        raise RuntimeError(f"SLEUTH_LOG_TRACE_CONFIG file not found: {path}")
    if not path and not _has_cmb_env():
        raise RuntimeError(
            "SLEUTH_LOG_TRACE=1 needs SLEUTH_LOG_TRACE_CONFIG (toml like eg_config.toml) "
            "or CMB_BUSINESSID / CMB_CAAS_DEPLOYUNITID / CMB_CAAS_SERVICEUNITID"
        )

    verbose = _verbose(config)
    async_mode = _async_mode(config)
    if path:
        _tracer = FastapiLogTrace(path, verbose=verbose, async_mode=async_mode)
    else:
        _tracer = FastapiLogTrace(verbose=verbose, async_mode=async_mode)

    lib_tracer = None
    try:
        lib_tracer = lib_get_tracer()
    except Exception:
        lib_tracer = None
    if lib_tracer is not None:
        _tracer = lib_tracer

    try:
        import log_trace.http_client  # noqa: F401 — patches httpx.Client.send
    except Exception:
        pass

    _install_log_handler()
    _ready = True


def trace_log(message: str, level: str = "INFO", **_tags: Any) -> None:
    tracer = _tracer
    if tracer is None:
        logging.getLogger("sleuth").log(
            getattr(logging, level if level != "WARN" else "WARNING", logging.INFO),
            message,
        )
        return
    try:
        tracer.log(message, level)
    except Exception:
        logging.getLogger("sleuth").info(message)


def return_code_headers(code: str = "SUC0000") -> dict:
    tracer = _tracer
    if tracer is None:
        return {}
    key = getattr(tracer, "return_code", None) or "x-b3-returnCode"
    return {str(key): str(code)}


def envelope_uses_content() -> bool:
    return is_enabled() and _tracer is not None


@contextmanager
def trace_span(host: str = "sleuth", api: str = "JOB:/internal") -> Iterator[None]:
    """eg_general: init_context + work + trace. No-op when disabled or already in a request."""
    tracer = _tracer
    if tracer is None:
        yield
        return
    if _has_request_context(tracer):
        yield
        return
    tracer.init_context({}, host, api)
    code = "SUC0000"
    try:
        yield
    except Exception:
        code = "ERROR"
        raise
    finally:
        try:
            tracer.trace(code)
        except Exception:
            pass


class ConvertSleuthAppErrorMiddleware:
    """Inner ASGI wrapper: sleuth.APPError -> log_trace.APPError(msg, code)."""

    def __init__(self, app: Any):
        self.app = app

    def add_exception_handler(self, *args: Any, **kwargs: Any) -> Any:
        return self.app.add_exception_handler(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        from .bizerror import APPError as SleuthAPPError

        try:
            await self.app(scope, receive, send)
        except SleuthAPPError as exc:
            from log_trace import APPError as TraceAPPError

            raise TraceAPPError(exc.msg, exc.code) from exc


def attach_starlette(app: Any, config: Any = None) -> Any:
    """Outer FastapiLogTraceMiddleware + inner APPError conversion."""
    if not is_enabled(config):
        return app
    ensure_initialized(config)
    from log_trace.fastapi_log_trace import FastapiLogTraceMiddleware

    app.add_middleware(ConvertSleuthAppErrorMiddleware)
    app.add_middleware(
        FastapiLogTraceMiddleware,
        ignore_path=ignore_paths(config),
    )
    return app


def install_mcp_middleware(server: Any, config: Any = None) -> None:
    if not is_enabled(config):
        return
    ensure_initialized(config)
    tracer = get_tracer()
    if tracer is None:
        return
    orig = getattr(server, "streamable_http_app", None)
    if not callable(orig):
        return

    def wrapped():
        from log_trace.fastmcp_log_trace import FastMcpLogTraceMiddleware

        app = orig()
        app.add_middleware(FastMcpLogTraceMiddleware, tracer=tracer)
        return app

    server.streamable_http_app = wrapped  # type: ignore[method-assign]


def traced_httpx():
    """Return log_trace.http_client.httpx when initialized, else stdlib httpx."""
    if _ready:
        try:
            from log_trace.http_client import httpx as traced

            return traced
        except Exception:
            pass
    import httpx

    return httpx


def mysql_enabled(config: Any = None) -> bool:
    if not is_enabled(config):
        return False
    ensure_initialized(config)
    return _tracer is not None


def reset_for_tests() -> None:
    global _tracer, _ready, _handler_installed
    _tracer = None
    _ready = False
    _handler_installed = False
