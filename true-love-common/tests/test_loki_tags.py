"""Tags known only after startup (like the bot a base finds logged in) reach every later log record."""

import logging
import unittest

from true_love_common.observability.logging import LoggingConfig


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class LokiTagTests(unittest.TestCase):
    def setUp(self):
        self.handler = Capture()
        logging.getLogger().addHandler(self.handler)
        self.addCleanup(logging.getLogger().removeHandler, self.handler)
        self.bot_id = ""
        LoggingConfig.add_loki_tags(lambda: {"bot_id": self.bot_id})

    def test_tag_is_added_once_known(self):
        logging.getLogger("x").warning("before")
        self.bot_id = "wxid_first"
        logging.getLogger("x").warning("after")

        before, after = self.handler.records
        self.assertFalse(hasattr(before, "tags"))
        self.assertEqual(after.tags, {"bot_id": "wxid_first"})


if __name__ == "__main__":
    unittest.main()
