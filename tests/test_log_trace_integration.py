"""log_trace adapter tests. Do not import tests/log_trace as the real package."""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

from sleuth.config import Config
from sleuth.logtrace import (
    ConvertSleuthAppErrorMiddleware,
    attach_starlette,
    config_file,
    ensure_initialized,
    envelope_uses_content,
    is_enabled,
    outbound_headers,
    reset_for_tests,
    return_code_headers,
    trace_span,
)
from sleuth.server.envelope import json_app, json_ok
from sleuth.server.streaming import run_prompt_in_thread


def _bool_env(name: str, value: str | None):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


class ConfigSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_for_tests()
        self._old = os.environ.get("SLEUTH_LOG_TRACE")
        self._old_cfg = os.environ.get("SLEUTH_LOG_TRACE_CONFIG")
        _bool_env("SLEUTH_LOG_TRACE", None)
        _bool_env("SLEUTH_LOG_TRACE_CONFIG", None)

    def tearDown(self) -> None:
        reset_for_tests()
        _bool_env("SLEUTH_LOG_TRACE", self._old)
        _bool_env("SLEUTH_LOG_TRACE_CONFIG", self._old_cfg)

    def test_disabled_by_default(self):
        self.assertFalse(is_enabled())
        cfg = Config()
        self.assertFalse(cfg.log_trace.enabled)

    def test_env_enables(self):
        os.environ["SLEUTH_LOG_TRACE"] = "1"
        os.environ["SLEUTH_LOG_TRACE_CONFIG"] = "log_trace.toml"
        from sleuth.config import _apply_env

        cfg = Config()
        _apply_env(cfg)
        self.assertTrue(cfg.log_trace.enabled)
        self.assertEqual(cfg.log_trace.config_file, "log_trace.toml")

    def test_jsonc_merge(self):
        cfg = Config()
        cfg.merge(
            {
                "log_trace": {
                    "enabled": True,
                    "config_file": "lt.toml",
                    "async_mode": False,
                    "verbose": True,
                    "ignore_paths": ["/health", "/ready"],
                }
            }
        )
        self.assertTrue(cfg.log_trace.enabled)
        self.assertEqual(cfg.log_trace.config_file, "lt.toml")
        self.assertFalse(cfg.log_trace.async_mode)
        self.assertEqual(cfg.log_trace.ignore_paths, ["/health", "/ready"])

    def test_ensure_initialized_fails_without_package(self):
        os.environ["SLEUTH_LOG_TRACE"] = "1"
        os.environ["CMB_BUSINESSID"] = "b"
        os.environ["CMB_CAAS_DEPLOYUNITID"] = "d"
        os.environ["CMB_CAAS_SERVICEUNITID"] = "s"
        with mock.patch.dict(sys.modules, {"log_trace": None, "log_trace.fastapi_log_trace": None}):
            with self.assertRaises(RuntimeError) as ctx:
                ensure_initialized()
        self.assertIn("log_trace", str(ctx.exception).lower())
        os.environ.pop("CMB_BUSINESSID", None)
        os.environ.pop("CMB_CAAS_DEPLOYUNITID", None)
        os.environ.pop("CMB_CAAS_SERVICEUNITID", None)


class _FakeTracer:
    return_code = "x-b3-returnCode"

    def __init__(self) -> None:
        self.inits = []
        self.traces = []
        self.logs = []
        self.headers_out = []
        self._tid = None

    def init_context(self, headers, host, api):
        self.inits.append((headers, host, api))
        self._tid = "tid"

    def _get_current_trace_id(self):
        return self._tid

    def get_next_headers(self):
        hdrs = {
            "x-b3-spanId": "span1",
            "x-b3-timestamp": "1",
            "x-b3-traceId": "trace1",
        }
        self.headers_out.append(hdrs)
        return hdrs

    def trace(self, code, extra_tags=None):
        self.traces.append(code)

    def log(self, message, level="INFO", error_stack=""):
        self.logs.append((str(message), level))

    def request(self, **kwargs):
        raise RuntimeError("not used")


class _FakeTraceAppError(Exception):
    def __init__(self, msg: str, code: str = "ERROR"):
        self.msg = msg
        self.code = code


class _FakeFastapiMw:
    def __init__(self, app, ignore_path=None, **kwargs):
        self.app = app
        self.ignore_path = ignore_path or []
        self.kwargs = kwargs

    def add_exception_handler(self, *args, **kwargs):
        return self.app.add_exception_handler(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


class _FakeMySql:
    last = None

    def __init__(self, config_path=None, config_section="MYSQL_CFG", **kwargs):
        _FakeMySql.last = {"config_path": config_path, "config_section": config_section, **kwargs}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self, *args, **kwargs):
        return self

    def execute(self, *args, **kwargs):
        return None

    def close(self):
        return None


class _FakePool:
    last = None

    def __init__(self, max_workers=1):
        self.max_workers = max_workers
        _FakePool.last = self
        self.submitted = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append(fn)
        fn(*args, **kwargs)


def _install_fake_log_trace(tracer=None):
    tracer = tracer or _FakeTracer()
    pkg = types.ModuleType("log_trace")
    pkg.APPError = _FakeTraceAppError
    pkg.ContextThreadPoolExecutor = _FakePool
    pkg.get_tracer = lambda: tracer
    fastapi_mod = types.ModuleType("log_trace.fastapi_log_trace")

    class FastapiLogTrace:
        def __new__(cls, *args, **kwargs):
            return tracer

    fastapi_mod.FastapiLogTrace = FastapiLogTrace
    fastapi_mod.FastapiLogTraceMiddleware = _FakeFastapiMw
    http_mod = types.ModuleType("log_trace.http_client")
    http_mod.httpx = types.SimpleNamespace(Client=object, AsyncClient=object)
    http_mod.add_http_callstack = lambda *a, **k: None
    mysql_mod = types.ModuleType("log_trace.mysql_log_trace")
    mysql_mod.MySql = _FakeMySql
    mysql_mod.DictCursor = object
    sys.modules["log_trace"] = pkg
    sys.modules["log_trace.fastapi_log_trace"] = fastapi_mod
    sys.modules["log_trace.http_client"] = http_mod
    sys.modules["log_trace.mysql_log_trace"] = mysql_mod
    return tracer


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_for_tests()
        self._old = os.environ.get("SLEUTH_LOG_TRACE")
        os.environ.pop("SLEUTH_LOG_TRACE_CONFIG", None)
        os.environ["SLEUTH_LOG_TRACE"] = "1"
        os.environ["CMB_BUSINESSID"] = "b"
        os.environ["CMB_CAAS_DEPLOYUNITID"] = "d"
        os.environ["CMB_CAAS_SERVICEUNITID"] = "s"
        self._mods = {
            k: sys.modules.get(k)
            for k in (
                "log_trace",
                "log_trace.fastapi_log_trace",
                "log_trace.http_client",
                "log_trace.mysql_log_trace",
            )
        }
        self.tracer = _install_fake_log_trace()
        ensure_initialized()

    def tearDown(self) -> None:
        reset_for_tests()
        _bool_env("SLEUTH_LOG_TRACE", self._old)
        for k in ("CMB_BUSINESSID", "CMB_CAAS_DEPLOYUNITID", "CMB_CAAS_SERVICEUNITID"):
            os.environ.pop(k, None)
        for name, mod in self._mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_json_ok_uses_content_and_header(self):
        self.assertTrue(envelope_uses_content())
        resp = json_ok({"ok": True})
        import json

        body = json.loads(resp.body.decode())
        self.assertEqual(body["code"], "SUC0000")
        self.assertEqual(body["content"], {"ok": True})
        self.assertNotIn("data", body)
        self.assertEqual(resp.headers.get("x-b3-returnCode"), "SUC0000")

    def test_app_error_envelope_uses_content(self):
        from sleuth.bizerror import APPError, BizErrorCode

        err = APPError.of(BizErrorCode.SESSION_NOT_FOUND, "s1", status=404)
        env = err.envelope()
        self.assertEqual(env["code"], "AMLS001")
        self.assertIn("content", env)
        self.assertNotIn("data", env)
        resp = json_app(err)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("x-b3-returnCode"), "AMLS001")

    def test_outbound_headers_merges_b3(self):
        merged = outbound_headers({"Accept": "application/json"})
        self.assertEqual(merged["Accept"], "application/json")
        self.assertEqual(merged["x-b3-spanId"], "span1")
        self.assertTrue(self.tracer.headers_out)

    def test_mcp_headers_inject(self):
        from sleuth.mcp.manager import McpManager

        mgr = McpManager(Config())
        hdrs = mgr._mcp_headers({"X-Custom": "1"})
        self.assertEqual(hdrs["X-Custom"], "1")
        self.assertEqual(hdrs["x-b3-spanId"], "span1")

    def test_return_code_headers(self):
        self.assertEqual(return_code_headers("SUC0000")["x-b3-returnCode"], "SUC0000")

    def test_trace_span_inits_and_traces(self):
        with trace_span(host="h", api="JOB:/x"):
            pass
        self.assertEqual(self.tracer.inits[-1][1], "h")
        self.assertEqual(self.tracer.traces[-1], "SUC0000")

    def test_trace_span_skips_when_context_exists(self):
        self.tracer._tid = "already"
        n = len(self.tracer.inits)
        with trace_span(host="h", api="JOB:/x"):
            pass
        self.assertEqual(len(self.tracer.inits), n)

    def test_convert_middleware_raises_log_trace_apperror(self):
        from sleuth.bizerror import APPError, BizErrorCode

        async def inner(scope, receive, send):
            raise APPError.of(BizErrorCode.SESSION_NOT_FOUND, "s1", status=404)

        mw = ConvertSleuthAppErrorMiddleware(inner)

        async def run():
            with self.assertRaises(_FakeTraceAppError) as ctx:
                await mw({"type": "http"}, None, None)
            self.assertEqual(ctx.exception.code, "AMLS001")

        import asyncio

        asyncio.run(run())

    def test_run_prompt_uses_context_pool(self):
        class R:
            def on_error(self, *a, **k):
                pass

            def close(self):
                self.closed = True

        class S:
            def prompt(self, p):
                self.got = p

        sess = S()
        rend = R()
        run_prompt_in_thread(sess, "hi", rend)
        self.assertTrue(getattr(rend, "closed", False))
        self.assertEqual(sess.got, "hi")
        self.assertIsNotNone(_FakePool.last)
        self.assertEqual(len(_FakePool.last.submitted), 1)


class AttachStarletteTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_for_tests()
        os.environ.pop("SLEUTH_LOG_TRACE_CONFIG", None)
        os.environ["SLEUTH_LOG_TRACE"] = "1"
        os.environ["CMB_BUSINESSID"] = "b"
        os.environ["CMB_CAAS_DEPLOYUNITID"] = "d"
        os.environ["CMB_CAAS_SERVICEUNITID"] = "s"
        self._mods = {
            k: sys.modules.get(k)
            for k in (
                "log_trace",
                "log_trace.fastapi_log_trace",
                "log_trace.http_client",
                "log_trace.mysql_log_trace",
            )
        }
        _install_fake_log_trace()
        ensure_initialized()

    def tearDown(self) -> None:
        reset_for_tests()
        os.environ.pop("SLEUTH_LOG_TRACE", None)
        for k in ("CMB_BUSINESSID", "CMB_CAAS_DEPLOYUNITID", "CMB_CAAS_SERVICEUNITID"):
            os.environ.pop(k, None)
        for name, mod in self._mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_attach_adds_middleware(self):
        class App:
            def __init__(self):
                self.added = []

            def add_middleware(self, cls, **kwargs):
                self.added.append((cls, kwargs))

            def add_exception_handler(self, *a, **k):
                pass

        app = App()
        attach_starlette(app)
        kinds = [c[0] for c in app.added]
        self.assertIn(ConvertSleuthAppErrorMiddleware, kinds)
        self.assertIn(_FakeFastapiMw, kinds)
        fastapi_kw = [k for c, k in app.added if c is _FakeFastapiMw][0]
        self.assertEqual(fastapi_kw.get("ignore_path"), ["/health"])
        self.assertNotIn("tracer", fastapi_kw)

    def test_ensure_initialized_uses_get_tracer_and_http_client(self):
        from sleuth.logtrace import get_tracer

        self.assertIs(get_tracer(), sys.modules["log_trace"].get_tracer())
        self.assertIn("log_trace.http_client", sys.modules)


class ConfigFileTests(unittest.TestCase):
    def test_config_file_from_object(self):
        cfg = Config()
        cfg.log_trace.config_file = "x.toml"
        self.assertEqual(config_file(cfg), "x.toml")


class MySqlPlaceholderTests(unittest.TestCase):
    def test_ph_question_when_log_trace(self):
        from sleuth.storage.mysql import MySQLStore

        store = object.__new__(MySQLStore)
        store._use_log_trace = True
        self.assertEqual(store._ph(), "?")
        self.assertEqual(store._ps(3), "?,?,?")
        store._use_log_trace = False
        self.assertEqual(store._ph(), "%s")
        self.assertEqual(store._ps(2), "%s,%s")

    def test_mysql_ctor_passes_config_path(self):
        reset_for_tests()
        os.environ.pop("SLEUTH_LOG_TRACE_CONFIG", None)
        os.environ["SLEUTH_LOG_TRACE"] = "1"
        os.environ["CMB_BUSINESSID"] = "b"
        os.environ["CMB_CAAS_DEPLOYUNITID"] = "d"
        os.environ["CMB_CAAS_SERVICEUNITID"] = "s"
        mods = {
            k: sys.modules.get(k)
            for k in (
                "log_trace",
                "log_trace.fastapi_log_trace",
                "log_trace.http_client",
                "log_trace.mysql_log_trace",
            )
        }
        _FakeMySql.last = None
        try:
            _install_fake_log_trace()
            ensure_initialized()
            from sleuth.storage.mysql import MySQLStore

            store = object.__new__(MySQLStore)
            store._use_log_trace = True
            store._MySql = _FakeMySql
            store._dict_cursor = object
            with mock.patch("sleuth.logtrace.config_file", return_value="lt.toml"):
                store._log_trace_conn()
            self.assertEqual(_FakeMySql.last["config_path"], "lt.toml")
            self.assertNotIn("tracer", _FakeMySql.last)
        finally:
            reset_for_tests()
            os.environ.pop("SLEUTH_LOG_TRACE", None)
            os.environ.pop("SLEUTH_LOG_TRACE_CONFIG", None)
            for k in ("CMB_BUSINESSID", "CMB_CAAS_DEPLOYUNITID", "CMB_CAAS_SERVICEUNITID"):
                os.environ.pop(k, None)
            for name, mod in mods.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod
