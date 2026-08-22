"""Tests for globally unique persistent IDs."""
from __future__ import annotations

import unittest

from sleuth.util.ids import file_id, message_id, part_id, session_id, tool_use_id


class PersistentIdTests(unittest.TestCase):
    def test_message_and_part_ids_are_unique_with_prefixes(self):
        msgs = [message_id() for _ in range(20)]
        parts = [part_id() for _ in range(20)]
        self.assertTrue(all(m.startswith("msg_") for m in msgs))
        self.assertTrue(all(p.startswith("part_") for p in parts))
        self.assertEqual(len(set(msgs)), 20)
        self.assertEqual(len(set(parts)), 20)
        self.assertTrue(set(msgs).isdisjoint(set(parts)))

    def test_session_and_tool_use_prefixes(self):
        self.assertTrue(session_id().startswith("sess_"))
        self.assertTrue(tool_use_id().startswith("toolu_"))
        self.assertTrue(file_id().startswith("file_"))
        self.assertNotEqual(session_id(), session_id())


if __name__ == "__main__":
    unittest.main()
