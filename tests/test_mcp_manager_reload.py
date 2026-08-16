"""MCP manager parallel connect / reload isolation tests."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sleuth.config import Config, McpServerConfig
from sleuth.mcp.manager import (
    McpManager,
    McpToolInfo,
    _exc_message,
    _mcp_loop_exception_handler,
    shutdown_manager,
)


class McpParallelConnectTests(unittest.TestCase):
    def tearDown(self):
        shutdown_manager()

    def test_hanging_server_does_not_block_other(self):
        cfg = Config(
            mcp_timeout={"startup": 2000, "per_server": 500},
            mcp_retry_seconds=0,
            mcp_servers={
                "slow": McpServerConfig(
                    name="slow", type="remote", url="http://127.0.0.1:9/mcp"
                ),
                "ok": McpServerConfig(
                    name="ok", type="remote", url="http://127.0.0.1:8/mcp"
                ),
            },
        )
        mgr = McpManager(cfg)

        async def fake_connect(srv):
            if srv.name == "slow":
                await asyncio.sleep(5)
                raise RuntimeError("should have been timed out")
            mgr._sessions[srv.name] = object()
            mgr._tools["ok_ping"] = McpToolInfo(
                server="ok",
                name="ping",
                qualified="ok_ping",
                description="ping",
            )

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            mgr._ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(
                mgr._connect_all(mgr._remote_servers()), mgr._loop
            )
            fut.result(timeout=10)

        try:
            self.assertIn("ok", mgr._sessions)
            self.assertNotIn("slow", mgr._sessions)
            self.assertIn("ok_ping", mgr.tools)
            self.assertTrue(any("slow" in e for e in mgr.errors))
            statuses = {s.name: s for s in mgr.server_statuses()}
            self.assertTrue(statuses["ok"].connected)
            self.assertFalse(statuses["slow"].connected)
        finally:
            mgr.close()

    def test_reload_clears_and_reconnects(self):
        cfg = Config(
            mcp_timeout={"per_server": 2000},
            mcp_retry_seconds=0,
            mcp_servers={
                "a": McpServerConfig(name="a", type="remote", url="http://x/mcp"),
            },
        )
        mgr = McpManager(cfg)
        calls = {"n": 0}

        async def fake_connect(srv):
            calls["n"] += 1
            mgr._sessions[srv.name] = object()
            mgr._tools["a_t"] = McpToolInfo(
                server="a", name="t", qualified="a_t", description="t"
            )

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            mgr.start()
            self.assertEqual(calls["n"], 1)
            self.assertIn("a", mgr._sessions)
            mgr.reload(cfg)
            self.assertEqual(calls["n"], 2)
            self.assertIn("a_t", mgr.tools)
        mgr.close()


class _ExcGroup(Exception):
    def __init__(self, msg, exceptions):
        super().__init__(msg)
        self.exceptions = exceptions


class McpErrorUnwrapTests(unittest.TestCase):
    def tearDown(self):
        shutdown_manager()

    def test_exc_message_skips_cancel_scope(self):
        scope = RuntimeError(
            "Attempted to exit cancel scope in a different task than it was entered in"
        )
        inner = ConnectionError("All connection attempts failed")
        msg = _exc_message(_ExcGroup("unhandled errors in a TaskGroup", [scope, inner]))
        self.assertIn("connection attempts", msg.lower())
        self.assertNotIn("cancel scope", msg.lower())

    def test_connect_records_inner_error_not_cancel_scope(self):
        cfg = Config(
            mcp_timeout={"per_server": 2000},
            mcp_retry_seconds=0,
            mcp_servers={
                "a": McpServerConfig(name="a", type="remote", url="http://127.0.0.1:9/mcp"),
            },
        )
        mgr = McpManager(cfg)
        scope = RuntimeError(
            "Attempted to exit cancel scope in a different task than it was entered in"
        )
        inner = ConnectionError("All connection attempts failed")
        group = _ExcGroup("unhandled errors in a TaskGroup", [scope, inner])

        async def fake_connect(srv):
            raise group

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            mgr._ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(
                mgr._connect_all(mgr._remote_servers()), mgr._loop
            )
            fut.result(timeout=10)

        self.assertTrue(any("connection attempts" in e.lower() for e in mgr.errors))
        self.assertFalse(any("cancel scope" in e.lower() for e in mgr.errors))
        statuses = {s.name: s for s in mgr.server_statuses()}
        self.assertFalse(statuses["a"].connected)
        mgr.close()

    def test_loop_handler_swallows_cancel_scope(self):
        seen = []

        class _Loop:
            def default_exception_handler(self, context):
                seen.append(context)

        loop = _Loop()
        _mcp_loop_exception_handler(
            loop,  # type: ignore[arg-type]
            {
                "message": "Task exception was never retrieved",
                "exception": RuntimeError(
                    "Attempted to exit cancel scope in a different task than it was entered in"
                ),
            },
        )
        self.assertEqual(seen, [])
        _mcp_loop_exception_handler(loop, {"message": "boom", "exception": ValueError("x")})  # type: ignore[arg-type]
        self.assertEqual(len(seen), 1)


class McpHotLoadTests(unittest.TestCase):
    def tearDown(self):
        shutdown_manager()

    def test_start_returns_before_hanging_connect(self):
        import time

        cfg = Config(
            mcp_timeout={"per_server": 30000},
            mcp_retry_seconds=0,
            mcp_servers={
                "slow": McpServerConfig(
                    name="slow", type="remote", url="http://127.0.0.1:9/mcp"
                ),
            },
        )
        mgr = McpManager(cfg)

        async def fake_connect(srv):
            await asyncio.sleep(20)

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            t0 = time.monotonic()
            mgr.start()
            elapsed = time.monotonic() - t0
        try:
            self.assertLess(elapsed, 5.0)
            self.assertTrue(mgr._started)
        finally:
            mgr.close()

    def test_retry_connects_disconnected_without_dropping_ok(self):
        cfg = Config(
            mcp_timeout={"per_server": 2000},
            mcp_retry_seconds=0,
            mcp_servers={
                "ok": McpServerConfig(name="ok", type="remote", url="http://127.0.0.1:8/mcp"),
                "down": McpServerConfig(
                    name="down", type="remote", url="http://127.0.0.1:9/mcp"
                ),
            },
        )
        mgr = McpManager(cfg)
        mgr._sessions["ok"] = object()

        async def fake_connect(srv):
            if srv.name == "ok":
                raise AssertionError("should not reconnect healthy server")
            mgr._sessions[srv.name] = object()
            mgr._tools["down_ping"] = McpToolInfo(
                server="down",
                name="ping",
                qualified="down_ping",
                description="ping",
            )

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            mgr._ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(mgr._retry_disconnected(), mgr._loop)
            fut.result(timeout=10)
        try:
            self.assertIn("ok", mgr._sessions)
            self.assertIn("down", mgr._sessions)
            self.assertIn("down_ping", mgr.tools)
        finally:
            mgr.close()

    def test_retry_replaces_server_error(self):
        cfg = Config(
            mcp_timeout={"per_server": 2000},
            mcp_retry_seconds=0,
            mcp_servers={
                "a": McpServerConfig(name="a", type="remote", url="http://127.0.0.1:9/mcp"),
            },
        )
        mgr = McpManager(cfg)
        n = {"i": 0}

        async def fake_connect(srv):
            n["i"] += 1
            if n["i"] == 1:
                raise ConnectionError("first fail")
            mgr._sessions[srv.name] = object()

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            mgr._ensure_loop()
            asyncio.run_coroutine_threadsafe(
                mgr._connect_all(mgr._remote_servers()), mgr._loop
            ).result(timeout=10)
            self.assertEqual(len(mgr.errors), 1)
            self.assertIn("first fail", mgr.errors[0])
            asyncio.run_coroutine_threadsafe(mgr._retry_disconnected(), mgr._loop).result(
                timeout=10
            )
        try:
            self.assertIn("a", mgr._sessions)
            self.assertEqual(mgr.errors, [])
        finally:
            mgr.close()

    def test_sync_session_picks_up_new_tools(self):
        from types import SimpleNamespace

        from sleuth.app import sync_session_mcp
        from sleuth.tools.registry import ToolRegistry

        cfg = Config(mcp_retry_seconds=0)
        mgr = McpManager(cfg)
        sess = SimpleNamespace(
            registry=ToolRegistry(tools=[]),
            config=cfg,
            _mcp_tool_names=set(),
            _mcp_card_names=set(),
            _mcp_manager=mgr,
        )
        self.assertFalse(sync_session_mcp(sess))
        mgr._tools["x_ping"] = McpToolInfo(
            server="x",
            name="ping",
            qualified="x_ping",
            description="ping",
        )
        self.assertTrue(sync_session_mcp(sess))
        self.assertIn("x_ping", sess.registry.names())
        self.assertEqual(sess._mcp_tool_names, {"x_ping"})

    def test_merge_retry_seconds(self):
        cfg = Config()
        self.assertEqual(cfg.mcp_retry_seconds, 15)
        cfg.merge({"mcp": {"retry_seconds": 7}})
        self.assertEqual(cfg.mcp_retry_seconds, 7)


if __name__ == "__main__":
    unittest.main()
