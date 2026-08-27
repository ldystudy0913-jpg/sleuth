"""Privacy gates and no-schema-migration guarantees for memory."""
from __future__ import annotations

import pathlib
import unittest

from sleuth.config import Config
from sleuth.memory.embed import HashEmbedder
from sleuth.memory.service import MemoryPrivacyError, write_memory
from sleuth.memory.store import InMemoryMemoryStore
from sleuth.privacy import contains_raw_pii


class MemoryPrivacyTests(unittest.TestCase):
    def test_contains_raw_pii(self):
        self.assertTrue(contains_raw_pii("证件号 110101199001011234"))
        self.assertFalse(contains_raw_pii("证件号 110***********34"))

    def test_write_rejects_id_card_and_skips_store(self):
        cfg = Config()
        store = InMemoryMemoryStore(cfg)
        cfg._memory_store = store
        cfg._embedder = HashEmbedder(cfg.memory.embedding_dim)
        with self.assertRaises(MemoryPrivacyError):
            write_memory(
                cfg,
                actor="u1",
                scope_kind="user",
                scope_id="u1",
                scenario_code="general",
                mem_kind="fact",
                item_key="customer.id",
                title_text="客户",
                body_text="证件号 110101199001011234",
                origin_type="user_explicit",
            )
        self.assertEqual(store.items, {})

    def test_item_key_must_be_catalog_domain_aspect(self):
        cfg = Config()
        cfg._memory_store = InMemoryMemoryStore(cfg)
        cfg._embedder = HashEmbedder(cfg.memory.embedding_dim)
        with self.assertRaisesRegex(ValueError, "domain.aspect"):
            write_memory(
                cfg,
                actor="u1",
                scope_kind="user",
                scope_id="u1",
                scenario_code="general",
                mem_kind="preference",
                item_key="Output-Language",
                title_text="语言",
                body_text="用中文回复",
                origin_type="user_explicit",
            )
        with self.assertRaisesRegex(ValueError, "must be one of"):
            write_memory(
                cfg,
                actor="u1",
                scope_kind="user",
                scope_id="u1",
                scenario_code="general",
                mem_kind="preference",
                item_key="output.invented",
                title_text="语言",
                body_text="用中文回复",
                origin_type="user_explicit",
            )
        item = write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="general",
            mem_kind="preference",
            item_key="output.language",
            title_text="语言",
            body_text="prefer chinese replies",
            origin_type="user_explicit",
        )
        again = write_memory(
            cfg,
            actor="u1",
            scope_kind="user",
            scope_id="u1",
            scenario_code="general",
            mem_kind="preference",
            item_key="output.language",
            title_text="语言",
            body_text="prefer chinese replies shorter",
            origin_type="user_explicit",
        )
        self.assertEqual(item.id, again.id)
        self.assertEqual(again.body_text, "prefer chinese replies shorter")

    def test_package_has_no_create_table(self):
        root = pathlib.Path(__file__).resolve().parents[1] / "sleuth" / "memory"
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            upper = text.upper()
            self.assertNotIn("CREATE TABLE IF", upper)
            self.assertNotIn("CREATE INDEX IF", upper)
            self.assertNotIn("CREATE TABLE MEM_", upper)

    def test_registry_includes_memory_tools(self):
        from sleuth.tools.registry import ToolRegistry

        names = ToolRegistry().names()
        self.assertIn("memory_search", names)
        self.assertIn("memory_write", names)
        self.assertIn("memory_forget", names)

    def test_og_schema_qualifies_table_and_skips_information_schema(self):
        from sleuth.memory import settings as memory_settings

        cfg = Config()
        cfg.memory.og_schema = "aml_gs"
        self.assertEqual(memory_settings.table_item_ref(cfg), "aml_gs.mem_item")
        self.assertEqual(memory_settings.table_audit_ref(cfg), "aml_gs.mem_audit")
        store = pathlib.Path(__file__).resolve().parents[1] / "sleuth" / "memory" / "store.py"
        text = store.read_text(encoding="utf-8")
        self.assertNotIn("information_schema.tables", text)
        self.assertNotIn("table_schema =", text)


if __name__ == "__main__":
    unittest.main()
