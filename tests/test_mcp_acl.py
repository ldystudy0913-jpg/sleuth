"""Agent-typed MCP tools stay on their agent; yolo does not bypass ACL."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from sleuth.app import _bind_session_mcp
from sleuth.catalog import skills_payload
from sleuth.config import AgentConfig, Config, McpServerConfig
from sleuth.mcp.access import mcp_server_owner_agent, session_may_use_owner_agent
from sleuth.mcp.agent_card import parse_agent_card
from sleuth.mcp.bridge import McpBridgeTool, bridge_tools
from sleuth.mcp.manager import McpToolInfo
from sleuth.memory.directory import InMemoryDirectory
from sleuth.memory.models import GrantRecord, OrgRecord, RoleRecord, UserRecord
from sleuth.permission import Permission, allow_all_rules, build_rules, evaluate
from sleuth.session import Session
from sleuth.skill import SkillInfo, set_skills
from sleuth.tools.base import ToolContext
from sleuth.tools.registry import ToolRegistry
from sleuth.tools.skill_tool import SkillTool


def _info(server="dd_reply", name="generate", qualified="dd_reply_generate"):
    return McpToolInfo(
        server=server,
        name=name,
        qualified=qualified,
        description="agent tool",
        input_schema={"type": "object", "properties": {}},
    )


class FakeMgr:
    def __init__(self, *, agent=True):
        self.tools = {
            "dd_reply_generate": _info(),
            "db_query": McpToolInfo(
                server="db",
                name="query",
                qualified="db_query",
                description="generic",
                input_schema={"type": "object", "properties": {}},
            ),
        }
        self.agent_cards = {"dd_reply": {"name": "dd_reply", "skills": []}}
        self.agent_card_servers = {"dd_reply": "dd_reply"}
        self.errors = []
        if not agent:
            self.agent_cards = {}
            self.agent_card_servers = {}

    def call_tool(self, qualified_name, arguments, progress_callback=None):
        return "ok", False


def _cfg(*, acl=True):
    cfg = Config(
        default_agent="build",
        agents={
            "build": AgentConfig(name="build"),
            "dd_reply": AgentConfig(name="dd_reply"),
        },
        mcp_servers={
            "dd_reply": McpServerConfig(
                name="dd_reply", url="http://127.0.0.1:9/mcp", agent=True
            ),
            "db": McpServerConfig(name="db", url="http://127.0.0.1:8/mcp", agent=False),
        },
        user_id="u1",
    )
    cfg.acl.enabled = acl
    cfg.acl.default_agent_open = True
    cfg.acl.default_agent_name = "build"
    cfg.skills.refresh_seconds = 0
    return cfg


def _dir(cfg: Config) -> InMemoryDirectory:
    d = InMemoryDirectory(cfg)
    d.orgs["SZ_BR"] = OrgRecord(
        org_id="SZ_BR", org_category="branch", org_name="深圳", row_status="active"
    )
    d.roles["aml_analyst"] = RoleRecord(
        role_id="aml_analyst", role_name="分析岗", row_status="active"
    )
    d.users["u1"] = UserRecord(
        user_id="u1", role_id="aml_analyst", org_id="SZ_BR", row_status="active"
    )
    cfg._directory = d
    return d


def _grant_agent(d: InMemoryDirectory):
    d.upsert_grant(
        GrantRecord(
            grant_id="g1",
            scope_kind="role",
            scope_id="aml_analyst",
            resource_kind="agent",
            resource_id="dd_reply",
            grant_effect="allow",
            row_status="active",
        )
    )


def _grant_skill(d: InMemoryDirectory, name="kyc-shared"):
    d.upsert_grant(
        GrantRecord(
            grant_id="gs",
            scope_kind="role",
            scope_id="aml_analyst",
            resource_kind="skill",
            resource_id=name,
            grant_effect="allow",
            row_status="active",
        )
    )


def _sess(cfg, agent="build", yolo=True):
    provider = MagicMock()
    provider.id = "openai"
    sess = Session(
        provider=provider,
        registry=ToolRegistry(),
        config=cfg,
        workdir=Path("."),
        permission=Permission(rules=allow_all_rules() if yolo else build_rules()),
        agent_name=agent,
        model_id="m",
        store=None,
        user_id=cfg.user_id,
        yolo=yolo,
    )
    return sess


class BuildRulesTests(unittest.TestCase):
    def test_no_hardcoded_ddreply_allow(self):
        rules = build_rules()
        self.assertEqual(evaluate("ddreply_generate_reply_framework", "*", rules).action, "ask")
        self.assertNotIn("ddreply_*", [r.permission for r in rules])


class McpOwnerTests(unittest.TestCase):
    def test_owner_only_for_agent_servers(self):
        cfg = _cfg()
        mgr = FakeMgr()
        self.assertEqual(mcp_server_owner_agent(cfg, mgr, "dd_reply"), "dd_reply")
        self.assertIsNone(mcp_server_owner_agent(cfg, mgr, "db"))

    def test_session_match_required_even_when_acl_off(self):
        cfg = _cfg(acl=False)
        sess = _sess(cfg, agent="build")
        self.assertFalse(session_may_use_owner_agent(sess, "dd_reply"))
        sess.agent_name = "dd_reply"
        self.assertTrue(session_may_use_owner_agent(sess, "dd_reply"))
        self.assertTrue(session_may_use_owner_agent(sess, None))


class BindFilterTests(unittest.TestCase):
    def test_build_hides_agent_mcp_even_with_grant_and_yolo(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        sess = _sess(cfg, agent="build", yolo=True)
        _bind_session_mcp(sess, FakeMgr())
        names = set(sess.registry.names())
        self.assertNotIn("dd_reply_generate", names)
        self.assertIn("db_query", names)

    def test_dd_reply_session_with_grant_sees_agent_tools(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        sess = _sess(cfg, agent="dd_reply", yolo=True)
        _bind_session_mcp(sess, FakeMgr())
        names = set(sess.registry.names())
        self.assertIn("dd_reply_generate", names)
        self.assertIn("db_query", names)

    def test_dd_reply_session_without_grant_hides_agent_tools(self):
        cfg = _cfg()
        _dir(cfg)
        sess = _sess(cfg, agent="dd_reply", yolo=True)
        _bind_session_mcp(sess, FakeMgr())
        names = set(sess.registry.names())
        self.assertNotIn("dd_reply_generate", names)
        self.assertIn("db_query", names)

    def test_execute_denies_mismatched_agent_even_if_registered(self):
        cfg = _cfg()
        _dir(cfg)
        sess = _sess(cfg, agent="build", yolo=True)
        mgr = FakeMgr()
        tool = McpBridgeTool(_info(), mgr)
        tool.owner_agent = "dd_reply"
        ctx = ToolContext(workdir=Path("."), permission=sess.permission, session=sess)
        result = tool.execute({}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("permission denied", result.output)

    def test_execute_allows_matching_agent_with_grant(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        sess = _sess(cfg, agent="dd_reply", yolo=True)
        mgr = FakeMgr()
        tool = McpBridgeTool(_info(), mgr)
        tool.owner_agent = "dd_reply"
        ctx = ToolContext(workdir=Path("."), permission=sess.permission, session=sess)
        result = tool.execute({}, ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(result.output, "ok")

    def test_set_agent_rebinds_mcp_tools(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        sess = _sess(cfg, agent="build", yolo=True)
        mgr = FakeMgr()
        sess._mcp_manager = mgr
        _bind_session_mcp(sess, mgr)
        self.assertNotIn("dd_reply_generate", sess.registry.names())
        sess.set_agent("dd_reply")
        self.assertIn("dd_reply_generate", sess.registry.names())
        sess.set_agent("build")
        self.assertNotIn("dd_reply_generate", sess.registry.names())
        self.assertIn("db_query", sess.registry.names())


class WildcardGrantMcpTests(unittest.TestCase):
    def _hq(self, cfg: Config) -> InMemoryDirectory:
        d = _dir(cfg)
        d.roles["hq_admin"] = RoleRecord(
            role_id="hq_admin", role_name="总行平台管理员", row_status="active"
        )
        d.users["u1"].role_id = "hq_admin"
        d.upsert_grant(
            GrantRecord(
                grant_id="wa",
                scope_kind="role",
                scope_id="hq_admin",
                resource_kind="agent",
                resource_id="*",
                grant_effect="allow",
                row_status="active",
            )
        )
        d.upsert_grant(
            GrantRecord(
                grant_id="ws",
                scope_kind="role",
                scope_id="hq_admin",
                resource_kind="skill",
                resource_id="*",
                grant_effect="allow",
                row_status="active",
            )
        )
        return d

    def test_wildcard_on_build_hides_agent_mcp_even_with_yolo(self):
        cfg = _cfg()
        self._hq(cfg)
        sess = _sess(cfg, agent="build", yolo=True)
        _bind_session_mcp(sess, FakeMgr())
        names = set(sess.registry.names())
        self.assertNotIn("dd_reply_generate", names)
        self.assertIn("db_query", names)
        tool = McpBridgeTool(_info(), FakeMgr())
        tool.owner_agent = "dd_reply"
        ctx = ToolContext(workdir=Path("."), permission=sess.permission, session=sess)
        result = tool.execute({}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("permission denied", result.output)

    def test_wildcard_set_agent_then_tools_work(self):
        cfg = _cfg()
        self._hq(cfg)
        sess = _sess(cfg, agent="build", yolo=True)
        mgr = FakeMgr()
        sess._mcp_manager = mgr
        _bind_session_mcp(sess, mgr)
        self.assertNotIn("dd_reply_generate", sess.registry.names())
        sess.set_agent("dd_reply")
        self.assertIn("dd_reply_generate", sess.registry.names())
        tool = McpBridgeTool(_info(), mgr)
        tool.owner_agent = "dd_reply"
        ctx = ToolContext(workdir=Path("."), permission=sess.permission, session=sess)
        result = tool.execute({}, ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(result.output, "ok")


class SkillCatalogTests(unittest.TestCase):
    def tearDown(self):
        set_skills({})

    def test_pinnable_catalog_and_private_card_skill(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        _grant_skill(d, "kyc-shared")
        set_skills(
            {
                "kyc-shared": SkillInfo(
                    name="kyc-shared",
                    description="shared",
                    location=Path("cos/kyc-shared/SKILL.md"),
                    content="# shared",
                ),
                "dd-reply-framework": SkillInfo(
                    name="dd-reply-framework",
                    description="private",
                    location=Path("mcp_agent/dd_reply/dd-reply-framework/SKILL.md"),
                    content="# private",
                    owner_agent="dd_reply",
                ),
            }
        )
        rows = skills_payload(cfg, Path("."), user_id="u1", mcp_manager=FakeMgr())
        by_name = {r["name"]: r for r in rows}
        self.assertTrue(by_name["kyc-shared"]["pinnable"])
        self.assertFalse(by_name["dd-reply-framework"]["pinnable"])
        self.assertEqual(by_name["dd-reply-framework"]["owner_agent"], "dd_reply")

    def test_private_skill_hidden_without_agent_grant(self):
        cfg = _cfg()
        _dir(cfg)
        set_skills(
            {
                "dd-reply-framework": SkillInfo(
                    name="dd-reply-framework",
                    description="private",
                    location=Path("mcp_agent/x/SKILL.md"),
                    content="# private",
                    owner_agent="dd_reply",
                )
            }
        )
        rows = skills_payload(cfg, Path("."), user_id="u1", mcp_manager=FakeMgr())
        self.assertEqual(rows, [])

    def test_card_name_only_lists_skill_names_for_reuse(self):
        agent, skills = parse_agent_card(
            {
                "name": "dd_reply",
                "skills": [{"name": "kyc-shared"}, {"name": "private", "content": "# p"}],
            },
            server_name="dd_reply",
        )
        self.assertEqual(agent.skill_names, ["kyc-shared", "private"])
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].owner_agent, "dd_reply")

    def test_inject_catalog_skill_requires_skill_grant(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        cfg.agents["dd_reply"].skill_names = ["kyc-shared"]
        set_skills(
            {
                "kyc-shared": SkillInfo(
                    name="kyc-shared",
                    description="shared",
                    location=Path("cos/kyc-shared/SKILL.md"),
                    content="# KYC SOP",
                )
            }
        )
        sess = _sess(cfg, agent="dd_reply")
        self.assertEqual(sess._bound_skill_names(), [])
        _grant_skill(d, "kyc-shared")
        self.assertEqual(sess._bound_skill_names(), ["kyc-shared"])
        text = sess._agent_skill_prompt()
        self.assertIn("KYC SOP", text)

    def test_inject_private_skill_with_agent_grant_only(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        set_skills(
            {
                "dd-reply-framework": SkillInfo(
                    name="dd-reply-framework",
                    description="private",
                    location=Path("mcp_agent/x/SKILL.md"),
                    content="# private SOP",
                    owner_agent="dd_reply",
                )
            }
        )
        sess = _sess(cfg, agent="dd_reply")
        self.assertEqual(sess._bound_skill_names(), ["dd-reply-framework"])

    def test_cannot_pin_private_skill_on_build(self):
        cfg = _cfg(acl=False)
        set_skills(
            {
                "dd-reply-framework": SkillInfo(
                    name="dd-reply-framework",
                    description="private",
                    location=Path("mcp_agent/x/SKILL.md"),
                    content="# private",
                    owner_agent="dd_reply",
                )
            }
        )
        sess = _sess(cfg, agent="build")
        with self.assertRaises(ValueError):
            sess.set_skills(["dd-reply-framework"])

    def test_skill_tool_blocks_foreign_private(self):
        cfg = _cfg(acl=False)
        set_skills(
            {
                "dd-reply-framework": SkillInfo(
                    name="dd-reply-framework",
                    description="private",
                    location=Path("mcp_agent/x/SKILL.md"),
                    content="# private",
                    owner_agent="dd_reply",
                )
            }
        )
        sess = _sess(cfg, agent="build")
        ctx = ToolContext(workdir=Path("."), permission=sess.permission, session=sess)
        result = SkillTool().execute({"name": "dd-reply-framework"}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("private", result.output)

    def test_refresh_reapplies_card_private_skill(self):
        from sleuth.catalog import merge_live_mcp_skills
        from sleuth.skill import get_skills

        cfg = _cfg(acl=False)
        mgr = FakeMgr()
        mgr.agent_cards = {
            "dd_reply": {
                "name": "dd_reply",
                "skills": [
                    {"name": "dd-reply-framework", "content": "# private SOP"}
                ],
                "mcp_server": "dd_reply",
            }
        }
        set_skills({})
        merge_live_mcp_skills(cfg, mgr)
        info = get_skills().get("dd-reply-framework")
        self.assertIsNotNone(info)
        self.assertEqual(info.owner_agent, "dd_reply")
        self.assertIn("private SOP", info.content)

    def test_two_agents_reuse_same_catalog_skill(self):
        cfg = _cfg()
        d = _dir(cfg)
        _grant_agent(d)
        d.upsert_grant(
            GrantRecord(
                grant_id="g2",
                scope_kind="role",
                scope_id="aml_analyst",
                resource_kind="agent",
                resource_id="dd_analyst",
                grant_effect="allow",
                row_status="active",
            )
        )
        _grant_skill(d, "kyc-shared")
        cfg.agents["dd_analyst"] = AgentConfig(
            name="dd_analyst", skill_names=["kyc-shared"]
        )
        cfg.agents["dd_reply"].skill_names = ["kyc-shared"]
        set_skills(
            {
                "kyc-shared": SkillInfo(
                    name="kyc-shared",
                    description="shared",
                    location=Path("cos/kyc-shared/SKILL.md"),
                    content="# KYC SOP",
                )
            }
        )
        a = _sess(cfg, agent="dd_reply")
        b = _sess(cfg, agent="dd_analyst")
        self.assertEqual(a._bound_skill_names(), ["kyc-shared"])
        self.assertEqual(b._bound_skill_names(), ["kyc-shared"])
        self.assertIn("KYC SOP", a._agent_skill_prompt())
        self.assertIn("KYC SOP", b._agent_skill_prompt())


class BridgeFilterTests(unittest.TestCase):
    def test_bridge_tools_filters_with_session(self):
        cfg = _cfg()
        _dir(cfg)
        sess = _sess(cfg, agent="build")
        tools = bridge_tools(FakeMgr(), session=sess)
        names = {t.name for t in tools}
        self.assertEqual(names, {"db_query"})


if __name__ == "__main__":
    unittest.main()
