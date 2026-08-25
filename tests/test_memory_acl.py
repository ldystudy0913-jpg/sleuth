"""Role-centric ACL: one user, one role, one org; grants not per-user explosion."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.catalog import agents_payload
from sleuth.config import AgentConfig, Config
from sleuth.memory.acl import attach_identity, resource_allowed
from sleuth.memory.directory import InMemoryDirectory, SqlDirectory, directory_for
from sleuth.memory.models import GrantRecord, OrgRecord, RoleRecord, UserRecord
from sleuth.session import Session


def _cfg(**kwargs) -> Config:
    cfg = Config(
        default_agent="build",
        agents={
            "build": AgentConfig(name="build"),
            "dd_reply": AgentConfig(name="dd_reply"),
        },
        **kwargs,
    )
    cfg.acl.enabled = True
    cfg.acl.default_agent_open = True
    cfg.acl.default_agent_name = "build"
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


class AclGrantTests(unittest.TestCase):
    def test_default_agent_open_without_user_row(self):
        cfg = _cfg()
        d = InMemoryDirectory(cfg)
        cfg._directory = d
        self.assertTrue(resource_allowed(cfg, "ghost", "agent", "build"))
        self.assertFalse(resource_allowed(cfg, "ghost", "agent", "dd_reply"))

    def test_role_grant_enables_agent_for_all_on_that_role(self):
        cfg = _cfg()
        d = _dir(cfg)
        d.upsert_grant(
            GrantRecord(
                grant_id="",
                scope_kind="role",
                scope_id="aml_analyst",
                resource_kind="agent",
                resource_id="dd_reply",
                grant_effect="allow",
                row_status="active",
            )
        )
        d.users["u2"] = UserRecord(
            user_id="u2", role_id="aml_analyst", org_id="SZ_BR", row_status="active"
        )
        self.assertTrue(resource_allowed(cfg, "u1", "agent", "dd_reply"))
        self.assertTrue(resource_allowed(cfg, "u2", "agent", "dd_reply"))

    def test_user_deny_overrides_role_allow(self):
        cfg = _cfg()
        d = _dir(cfg)
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
        d.upsert_grant(
            GrantRecord(
                grant_id="g2",
                scope_kind="user",
                scope_id="u1",
                resource_kind="agent",
                resource_id="dd_reply",
                grant_effect="deny",
                row_status="active",
            )
        )
        self.assertFalse(resource_allowed(cfg, "u1", "agent", "dd_reply"))

    def test_change_role_column_switches_grants(self):
        cfg = _cfg()
        d = _dir(cfg)
        d.roles["dd_officer"] = RoleRecord(
            role_id="dd_officer", role_name="尽调", row_status="active"
        )
        d.upsert_grant(
            GrantRecord(
                grant_id="g1",
                scope_kind="role",
                scope_id="dd_officer",
                resource_kind="agent",
                resource_id="dd_reply",
                grant_effect="allow",
                row_status="active",
            )
        )
        self.assertFalse(resource_allowed(cfg, "u1", "agent", "dd_reply"))
        d.users["u1"].role_id = "dd_officer"
        self.assertTrue(resource_allowed(cfg, "u1", "agent", "dd_reply"))

    def test_catalog_hides_unauthorized_agent(self):
        cfg = _cfg()
        _dir(cfg)
        payload = agents_payload(cfg, user_id="u1")
        names = {a["name"] for a in payload["agents"]}
        self.assertIn("build", names)
        self.assertNotIn("dd_reply", names)

    def test_set_agent_rejects_unauthorized(self):
        cfg = _cfg()
        _dir(cfg)
        sess = Session(
            provider=MagicMock(),
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            agent_name="build",
            user_id="u1",
            store=None,
        )
        with patch("sleuth.app.build_permission", return_value=MagicMock()):
            with self.assertRaises(ValueError):
                sess.set_agent("dd_reply")

    def test_acl_off_keeps_all_visible(self):
        cfg = _cfg()
        cfg.acl.enabled = False
        _dir(cfg)
        payload = agents_payload(cfg, user_id="u1")
        names = {a["name"] for a in payload["agents"]}
        self.assertIn("dd_reply", names)

    def test_attach_identity_from_directory(self):
        cfg = _cfg()
        _dir(cfg)
        sess = Session(
            provider=MagicMock(),
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            user_id="u1",
        )
        attach_identity(sess)
        self.assertEqual(sess.role_id, "aml_analyst")
        self.assertEqual(sess.org_id, "SZ_BR")

    def test_env_and_jsonc_knobs_load(self):
        cfg = Config()
        cfg.merge(
            {
                "memory": {
                    "backend": "opengauss",
                    "top_k": 7,
                    "min_score": "0.4",
                    "table_item": "mem_item",
                    "og_schema": "aml_gs",
                },
                "acl": {"enabled": True, "default_agent_name": "build"},
            }
        )
        self.assertEqual(cfg.memory.backend, "opengauss")
        self.assertEqual(cfg.memory.top_k, 7)
        self.assertEqual(cfg.memory.min_score, "0.4")
        self.assertEqual(cfg.memory.og_schema, "aml_gs")
        self.assertTrue(cfg.acl.enabled)
        self.assertEqual(cfg.acl.default_agent_name, "build")


class SqlDirectoryProbeTests(unittest.TestCase):
    def test_missing_tables_degrade(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cfg = Config()
        cfg.storage.sqlite_path = tmp.name
        d = SqlDirectory(cfg)
        self.assertFalse(d.available())
        self.assertIsNone(directory_for(cfg).get_user("u1"))

    def test_hand_built_tables_roundtrip(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.executescript(
            """
            CREATE TABLE mem_org (
              org_id TEXT PRIMARY KEY, parent_id TEXT, org_category TEXT,
              org_name TEXT, row_status TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE mem_role (
              role_id TEXT PRIMARY KEY, role_name TEXT, scenario_list TEXT,
              row_status TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE mem_user (
              user_id TEXT PRIMARY KEY, display_name TEXT, role_id TEXT, org_id TEXT,
              row_status TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE mem_grant (
              grant_id TEXT PRIMARY KEY, scope_kind TEXT, scope_id TEXT,
              resource_kind TEXT, resource_id TEXT, grant_effect TEXT,
              row_status TEXT, created_at TEXT, updated_at TEXT
            );
            """
        )
        conn.close()
        cfg = Config()
        cfg.storage.sqlite_path = tmp.name
        d = SqlDirectory(cfg)
        self.assertTrue(d.available())
        d.upsert_user(
            UserRecord(
                user_id="u1",
                display_name="张三",
                role_id="aml_analyst",
                org_id="SZ_BR",
                row_status="active",
            )
        )
        rec = d.get_user("u1")
        self.assertEqual(rec.role_id, "aml_analyst")
        self.assertEqual(rec.org_id, "SZ_BR")


if __name__ == "__main__":
    unittest.main()
