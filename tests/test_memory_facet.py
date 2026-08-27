"""Same catalog key can store distinct meanings; near-duplicates merge."""
from __future__ import annotations

import math
import unittest

from sleuth.config import Config
from sleuth.memory import settings
from sleuth.memory.models import OrgRecord, RoleRecord, UserRecord
from sleuth.memory.directory import InMemoryDirectory
from sleuth.memory.resolve import retrieve_for_prompt, search_memories
from sleuth.memory.service import write_memory
from sleuth.memory.store import InMemoryMemoryStore


class KeywordEmbedder:
    """Orthogonal vectors for cash / night / chain so merge tests are deterministic."""

    dim = 8

    def embed(self, text: str):
        raw = (text or "").lower()
        vec = [0.0] * self.dim
        if "cash" in raw or "fifty" in raw:
            vec[0] = 1.0
        if "night" in raw or "scattered" in raw:
            vec[1] = 1.0
        if "chain" in raw or "counterpart" in raw:
            vec[2] = 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def _ready_cfg() -> Config:
    cfg = Config()
    cfg.memory.backend = "off"
    cfg.memory.merge_score = "0.85"
    cfg.memory.merge_across_scopes = True
    cfg.memory.min_score = "0.35"
    store = InMemoryMemoryStore(cfg)
    cfg._memory_store = store
    cfg._embedder = KeywordEmbedder()
    d = InMemoryDirectory(cfg)
    d.users["u1"] = UserRecord(
        user_id="u1", role_id="aml_analyst", org_id="SZ_BR", row_status="active"
    )
    d.roles["aml_analyst"] = RoleRecord(
        role_id="aml_analyst", role_name="aml", row_status="active"
    )
    d.orgs["SZ_BR"] = OrgRecord(org_id="SZ_BR", org_name="SZ", row_status="active")
    cfg._directory = d
    return cfg


def _write_str(cfg, *, actor="u1", scope_kind="user", scope_id="u1", title, body, lock=False):
    return write_memory(
        cfg,
        actor=actor,
        scope_kind=scope_kind,
        scope_id=scope_id,
        scenario_code="suspicious_analysis",
        mem_kind="policy",
        item_key="str.threshold",
        title_text=title,
        body_text=body,
        origin_type="user_explicit",
        lock_item_key=lock,
    )


class MemoryFacetTests(unittest.TestCase):
    def test_paraphrase_updates_same_row(self):
        cfg = _ready_cfg()
        first = _write_str(cfg, title="cash", body="watch cash over fifty thousand")
        second = _write_str(cfg, title="cash2", body="cash over fifty thousand must be flagged")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(cfg._memory_store.items), 1)
        self.assertTrue(second.item_key.startswith("str.threshold."))
        self.assertIn("must be flagged", second.body_text)

    def test_distinct_meanings_are_two_rows(self):
        cfg = _ready_cfg()
        cash = _write_str(cfg, title="cash", body="watch cash over fifty thousand")
        night = _write_str(cfg, title="night", body="night scattered inbound transfers")
        self.assertNotEqual(cash.id, night.id)
        self.assertEqual(len(cfg._memory_store.items), 2)
        self.assertEqual(settings.catalog_item_key(cash.item_key), "str.threshold")
        self.assertEqual(settings.catalog_item_key(night.item_key), "str.threshold")
        self.assertNotEqual(cash.item_key, night.item_key)

    def test_third_paraphrase_updates_matching_facet_only(self):
        cfg = _ready_cfg()
        cash = _write_str(cfg, title="cash", body="watch cash over fifty thousand")
        night = _write_str(cfg, title="night", body="night scattered inbound transfers")
        again = _write_str(cfg, title="night2", body="night scattered inbound must alert")
        self.assertEqual(again.id, night.id)
        self.assertNotEqual(again.id, cash.id)
        self.assertEqual(len(cfg._memory_store.items), 2)
        self.assertEqual(cfg._memory_store.get(cash.id).body_text, "watch cash over fifty thousand")

    def test_user_paraphrase_reuses_role_item_key(self):
        cfg = _ready_cfg()
        role = _write_str(
            cfg,
            actor="admin",
            scope_kind="role",
            scope_id="aml_analyst",
            title="chain",
            body="write funds chain then counterparties",
        )
        user = _write_str(
            cfg,
            title="chain-user",
            body="write funds chain then counterparties first",
        )
        self.assertEqual(user.item_key, role.item_key)
        self.assertEqual(user.scope_kind, "user")
        hits = retrieve_for_prompt(cfg, "u1", "write funds chain then counterparties")
        chosen = [h for h in hits if h.item_key == role.item_key]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].scope_kind, "user")

    def test_user_new_meaning_keeps_role_row(self):
        cfg = _ready_cfg()
        role = _write_str(
            cfg,
            actor="admin",
            scope_kind="role",
            scope_id="aml_analyst",
            title="chain",
            body="write funds chain then counterparties",
        )
        cash = _write_str(cfg, title="cash", body="watch cash over fifty thousand")
        self.assertNotEqual(cash.item_key, role.item_key)
        hits = retrieve_for_prompt(cfg, "u1", "watch cash over fifty thousand write funds chain")
        keys = {h.item_key for h in hits}
        self.assertIn(cash.item_key, keys)
        self.assertIn(role.item_key, keys)

    def test_search_keeps_two_str_threshold_facets(self):
        cfg = _ready_cfg()
        cash = _write_str(cfg, title="cash", body="watch cash over fifty thousand")
        night = _write_str(cfg, title="night", body="night scattered inbound transfers")
        hits = search_memories(cfg, "u1", "cash night scattered fifty")
        ids = {h.id for h in hits}
        self.assertIn(cash.id, ids)
        self.assertIn(night.id, ids)

    def test_post_rejects_invented_instance_suffix(self):
        cfg = _ready_cfg()
        with self.assertRaisesRegex(ValueError, "domain.aspect"):
            write_memory(
                cfg,
                actor="u1",
                scope_kind="user",
                scope_id="u1",
                scenario_code="suspicious_analysis",
                mem_kind="policy",
                item_key="str.threshold.abcd",
                title_text="cash",
                body_text="watch cash over fifty thousand",
                origin_type="user_explicit",
            )

    def test_lock_item_key_updates_that_row_only(self):
        cfg = _ready_cfg()
        cash = _write_str(cfg, title="cash", body="watch cash over fifty thousand")
        night = _write_str(cfg, title="night", body="night scattered inbound transfers")
        patched = write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="suspicious_analysis",
            mem_kind="policy",
            item_key=cash.item_key,
            title_text="cash",
            body_text="watch cash over eighty thousand",
            origin_type="user_explicit",
            lock_item_key=True,
        )
        self.assertEqual(patched.id, cash.id)
        self.assertEqual(cfg._memory_store.get(night.id).body_text, "night scattered inbound transfers")


if __name__ == "__main__":
    unittest.main()
