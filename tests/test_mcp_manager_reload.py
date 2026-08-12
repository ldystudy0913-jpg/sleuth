"""MCP manager parallel connect / reload isolation tests."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sleuth.config import Config, McpServerConfig
from sleuth.mcp.manager import McpManager, McpToolInfo, shutdown_manager


class McpParallelConnectTests(unittest.TestCase):
    def tearDown(self):
        shutdown_manager()

    def test_hanging_server_does_not_block_other(self):
        cfg = Config(
            mcp_timeout={"startup": 2000, "per_server": 500},
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

        self.assertIn("ok", mgr._sessions)
        self.assertNotIn("slow", mgr._sessions)
        self.assertIn("ok_ping", mgr.tools)
        self.assertTrue(any("slow" in e for e in mgr.errors))
        statuses = {s.name: s for s in mgr.server_statuses()}
        self.assertTrue(statuses["ok"].connected)
        self.assertFalse(statuses["slow"].connected)

    def test_reload_clears_and_reconnects(self):
        cfg = Config(
            mcp_timeout={"per_server": 2000},
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


if __name__ == "__main__":
    unittest.main()
