"""One configuration file serves every base; each base looks up the master of the account it finds logged in."""

import os
import tempfile
import unittest
from unittest.mock import patch

from true_love_base import configuration

BOTS = 'bots:\n  wxid_first:\n    master: "alice"\n  wxid_second:\n    master: "bob"\n  wxid_quiet: {}\n'


class BotConfigTests(unittest.TestCase):
    def load(self, bots=BOTS):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with open(os.path.join(directory.name, "config.yaml"), "w", encoding="utf-8") as fp:
            fp.write('http_token: "token"\n' + bots)
        previous = os.getcwd()
        os.chdir(directory.name)
        self.addCleanup(os.chdir, previous)
        with (
            patch.object(configuration.LoggingConfig, "setup"),
            patch.object(configuration.Config, "_instance", None),
            patch.object(configuration.Config, "_initialized", False),
        ):
            return configuration.Config()

    def test_each_account_finds_its_own_master(self):
        config = self.load()

        self.assertEqual(config.master_of("wxid_first"), "alice")
        self.assertEqual(config.master_of("wxid_second"), "bob")

    def test_account_without_a_master_or_missing_from_the_map_has_none(self):
        config = self.load()

        for bot_id in ("wxid_quiet", "wxid_unknown", ""):
            with self.subTest(bot_id=bot_id):
                self.assertEqual(config.master_of(bot_id), "")

    def test_file_without_a_bots_map_still_loads(self):
        self.assertEqual(self.load(bots="").master_of("wxid_first"), "")


if __name__ == "__main__":
    unittest.main()
