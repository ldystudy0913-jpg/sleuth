"""Knowledge-base harvest status on long-term memory rows."""
from __future__ import annotations

import unittest

from sleuth.config import Config
from sleuth.memory.embed import HashEmbedder
from sleuth.memory.service import (
    filter_by_kb_status,
    set_kb_harvest,
    write_memory,
)
from sleuth.memory.store import InMemoryMemoryStore


def _cfg():
    cfg = Config()
    store = InMemoryMemoryStore(cfg)
    cfg._memory_store = store
    cfg._embedder = HashEmbedder(cfg.memory.embedding_dim)
    return cfg, store


def _write(cfg, body="prefer chinese replies", key="output.language"):
    return write_memory(
        cfg,
        actor="u1",
        scope_kind="user",
        scope_id="u1",
        scenario_code="general",
        mem_kind="preference",
        item_key=key,
        title_text="语言",
        body_text=body,
        origin_type="user_explicit",
    )


class MemoryKbHarvestTests(unittest.TestCase):
    def test_write_defaults_to_none(self):
        cfg, _store = _cfg()
        item = _write(cfg)
        self.assertEqual(item.kb_status, "none")
        self.assertIsNone(item.kb_ref)
        public = item.to_public_dict()
        self.assertEqual(public["kb_status"], "none")
        self.assertIsNone(public["kb_ref"])
        self.assertIsNone(public["kb_ingested_at"])

    def test_nominate_then_ingest(self):
        cfg, store = _cfg()
        item = _write(cfg)
        nominated = set_kb_harvest(
            cfg, item.id, actor="u1", kb_status="nominated", store=store
        )
        self.assertEqual(nominated.kb_status, "nominated")
        ingested = set_kb_harvest(
            cfg,
            item.id,
            actor="admin",
            kb_status="ingested",
            kb_ref="kb://policy/output-language",
            update_ref=True,
            store=store,
        )
        self.assertEqual(ingested.kb_status, "ingested")
        self.assertEqual(ingested.kb_ref, "kb://policy/output-language")
        self.assertIsNotNone(ingested.kb_ingested_at)
        self.assertEqual(ingested.kb_ingested_by, "admin")
        self.assertEqual(store.audits[-1]["action_type"], "kb_harvest")

    def test_invalid_kb_status_rejected(self):
        cfg, store = _cfg()
        item = _write(cfg)
        with self.assertRaisesRegex(ValueError, "kb_status"):
            set_kb_harvest(cfg, item.id, actor="u1", kb_status="yes", store=store)

    def test_content_update_marks_ingested_stale(self):
        cfg, store = _cfg()
        item = _write(cfg)
        set_kb_harvest(
            cfg,
            item.id,
            actor="admin",
            kb_status="ingested",
            kb_ref="kb-doc-1",
            update_ref=True,
            store=store,
        )
        again = _write(cfg, body="prefer chinese replies shorter")
        self.assertEqual(again.id, item.id)
        self.assertEqual(again.kb_status, "stale")
        self.assertEqual(again.kb_ref, "kb-doc-1")
        self.assertEqual(again.body_text, "prefer chinese replies shorter")

    def test_content_update_keeps_nominated(self):
        cfg, store = _cfg()
        item = _write(cfg)
        set_kb_harvest(cfg, item.id, actor="u1", kb_status="nominated", store=store)
        again = _write(cfg, body="prefer chinese replies shorter")
        self.assertEqual(again.kb_status, "nominated")

    def test_filter_by_kb_status(self):
        cfg, store = _cfg()
        none_item = _write(cfg)
        named = _write(cfg, body="always list counterparties first", key="str.narrative")
        set_kb_harvest(cfg, named.id, actor="u1", kb_status="nominated", store=store)
        items = list(store.items.values())
        nominated = filter_by_kb_status(cfg, items, "nominated")
        self.assertEqual([i.id for i in nominated], [named.id])
        leftover = filter_by_kb_status(cfg, items, "none")
        self.assertEqual([i.id for i in leftover], [none_item.id])
        with self.assertRaisesRegex(ValueError, "kb_status"):
            filter_by_kb_status(cfg, items, "done")

    def test_item_select_includes_kb_columns(self):
        from sleuth.memory.store import _ITEM_COLS

        for col in ("kb_status", "kb_ref", "kb_ingested_at", "kb_ingested_by"):
            self.assertIn(col, _ITEM_COLS)


if __name__ == "__main__":
    unittest.main()
