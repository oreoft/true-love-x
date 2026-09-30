"""tl-admin reads what AI remembers about one person in one chat of one bot."""

import unittest

from true_love_ai.memory import memory_manager
from ai_db import memory_db


class SessionMemoryTests(unittest.TestCase):
    def setUp(self):
        memory_db(self)

    def test_one_person_is_read_at_a_time_with_readable_labels(self):
        memory_manager.upsert_user_memory("bot_a:room", "alice", [{"key": "interest.music", "value": "jazz"}])
        memory_manager.upsert_user_memory("bot_a:room", "bob", [{"key": "occupation", "value": "chef"}])
        memory_manager.upsert_user_memory("bot_b:room", "alice", [{"key": "occupation", "value": "pilot"}])

        facts = memory_manager.describe_user_memory("bot_a:room", "alice")

        self.assertEqual([(f["label"], f["value"]) for f in facts], [("兴趣(music)", "jazz")])
        self.assertEqual(memory_manager.describe_user_memory("bot_a:room", "nobody"), [])


if __name__ == "__main__":
    unittest.main()
