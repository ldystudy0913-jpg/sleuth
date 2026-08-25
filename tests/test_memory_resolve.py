"""Vector recall, key override, pins, expiry, and graceful disable."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sleuth.config import Config
from sleuth.memory.embed import HashEmbedder
from sleuth.memory.models import MemoryItem, UserRecord
from sleuth.memory.prompt import format_memory_block, memory_prompt_block
from sleuth.memory.resolve import retrieve_for_prompt, search_memories
from sleuth.memory.service import write_memory
from sleuth.memory.store import InMemoryMemoryStore, utc_now
from sleuth.memory.directory import InMemoryDirectory


def _ready_cfg() -> Config:
    cfg = Config()
    cfg.memory.backend = "off"
    cfg.memory.min_score = "0.35"
    cfg.memory.top_k = 12
    cfg.memory.max_items = 24
    cfg.memory.max_chars = 6000
    cfg.memory.pin_kinds = "preference,forget"
    cfg._memory_store = InMemoryMemoryStore(cfg)
    cfg._embedder = HashEmbedder(cfg.memory.embedding_dim)
    d = InMemoryDirectory(cfg)
    d.users["u1"] = UserRecord(
        user_id="u1", role_id="aml_analyst", org_id="SZ_BR", row_status="active"
    )
    cfg._directory = d
    return cfg


def _item(**kwargs) -> MemoryItem:
    now = utc_now()
    data = dict(
        id="",
        scope_kind="user",
        scope_id="u1",
        scenario_code="general",
        mem_kind="fact",
        item_key="k",
        title_text="t",
        body_text="b",
        origin_type="admin",
        row_status="active",
        created_at=now,
        updated_at=now,
        created_by="u1",
        updated_by="u1",
    )
    data.update(kwargs)
    return MemoryItem(**data)


class MemoryResolveTests(unittest.TestCase):
    def test_user_overrides_role_overrides_org_same_key(self):
        cfg = _ready_cfg()
        store: InMemoryMemoryStore = cfg._memory_store
        embedder: HashEmbedder = cfg._embedder
        text = "str filing threshold uses local branch note"
        vec = embedder.embed(text)
        for scope, sid, body in (
            ("org", "SZ_BR", "org level threshold"),
            ("role", "aml_analyst", "role level threshold"),
            ("user", "u1", "user level threshold"),
        ):
            store.upsert(
                _item(
                    scope_kind=scope,
                    scope_id=sid,
                    item_key="str.threshold",
                    title_text="threshold",
                    body_text=body,
                    embedding=vec,
                    mem_kind="policy",
                ),
                actor="admin",
                action_type="create",
            )
        hits = retrieve_for_prompt(cfg, "u1", text)
        keys = [h.item_key for h in hits]
        self.assertIn("str.threshold", keys)
        chosen = next(h for h in hits if h.item_key == "str.threshold")
        self.assertEqual(chosen.scope_kind, "user")
        self.assertIn("user level", chosen.body_text)

    def test_semantic_query_ranks_matching_memory(self):
        cfg = _ready_cfg()
        write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="general",
            mem_kind="preference",
            item_key="output.language",
            title_text="language",
            body_text="prefer chinese replies for analysis notes",
            origin_type="user_explicit",
        )
        write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="suspicious_analysis",
            mem_kind="workflow",
            item_key="str.steps",
            title_text="steps",
            body_text="list counterparties then cash intensity then narrative",
            origin_type="agent_inferred",
        )
        hits = search_memories(cfg, "u1", "what language for chinese replies")
        self.assertTrue(hits)
        self.assertEqual(hits[0].item_key, "output.language")

    def test_low_score_dropped_but_user_preference_pinned(self):
        cfg = _ready_cfg()
        cfg.memory.min_score = "0.99"
        store: InMemoryMemoryStore = cfg._memory_store
        dim = cfg.memory.embedding_dim
        unrelated = [0.0] * dim
        unrelated[0] = 1.0
        store.upsert(
            _item(
                item_key="branch.policy",
                mem_kind="policy",
                title_text="policy",
                body_text="branch wide unused policy text",
                embedding=unrelated,
                scope_kind="org",
                scope_id="SZ_BR",
            ),
            actor="admin",
            action_type="create",
        )
        store.upsert(
            _item(
                item_key="output.language",
                mem_kind="preference",
                title_text="language",
                body_text="always use chinese",
                embedding=unrelated,
                scope_kind="user",
                scope_id="u1",
            ),
            actor="u1",
            action_type="create",
        )
        query = [0.0] * dim
        query[-1] = 1.0
        cfg._embedder = type("E", (), {"embed": lambda self, text: query})()
        hits = retrieve_for_prompt(cfg, "u1", "anything")
        keys = {h.item_key for h in hits}
        self.assertIn("output.language", keys)
        self.assertNotIn("branch.policy", keys)

    def test_expired_and_archived_skipped(self):
        cfg = _ready_cfg()
        store: InMemoryMemoryStore = cfg._memory_store
        embedder: HashEmbedder = cfg._embedder
        text = "cash intensive pattern for mule accounts"
        vec = embedder.embed(text)
        expired = store.upsert(
            _item(
                item_key="pattern.old",
                mem_kind="pattern",
                body_text=text,
                embedding=vec,
                expire_at=utc_now() - timedelta(days=1),
            ),
            actor="u1",
            action_type="create",
        )
        live = store.upsert(
            _item(
                item_key="pattern.live",
                mem_kind="pattern",
                body_text=text,
                embedding=vec,
            ),
            actor="u1",
            action_type="create",
        )
        store.archive(expired.id, actor="u1", action_type="forget")
        store.upsert(
            _item(
                id=expired.id,
                item_key="pattern.old",
                mem_kind="pattern",
                body_text=text,
                embedding=vec,
                expire_at=datetime(2000, 1, 1),
                row_status="archived",
            ),
            actor="u1",
            action_type="update",
        )
        hits = search_memories(cfg, "u1", text)
        ids = {h.id for h in hits}
        self.assertIn(live.id, ids)
        self.assertNotIn(expired.id, ids)

    def test_memory_off_without_store_or_embedder(self):
        cfg = Config()
        self.assertEqual(retrieve_for_prompt(cfg, "u1", "hello"), [])
        self.assertEqual(format_memory_block([]), "")

    def test_prompt_block_includes_recalled_text(self):
        cfg = _ready_cfg()
        write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="general",
            mem_kind="preference",
            item_key="output.language",
            title_text="language",
            body_text="prefer chinese replies",
            origin_type="user_explicit",
        )

        class Sess:
            config = cfg
            user_id = "u1"
            messages = []

        block = memory_prompt_block(Sess(), "chinese replies language")
        self.assertIn("Long-term memory", block)
        self.assertIn("output.language", block)

    def test_pattern_gets_ttl_from_config(self):
        cfg = _ready_cfg()
        cfg.memory.pattern_ttl_days = 90
        item = write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="suspicious_analysis",
            mem_kind="pattern",
            item_key="pattern.mule",
            title_text="mule",
            body_text="generalized cash intensive layering without names",
            origin_type="agent_inferred",
        )
        self.assertIsNotNone(item.expire_at)
        pref = write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="general",
            mem_kind="preference",
            item_key="output.language",
            title_text="language",
            body_text="prefer chinese replies",
            origin_type="user_explicit",
        )
        self.assertIsNone(pref.expire_at)


if __name__ == "__main__":
    unittest.main()
