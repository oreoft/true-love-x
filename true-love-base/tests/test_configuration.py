"""One configuration file serves every base; each base reads the entry of the bot it was deployed with."""

import os
import tempfile
import unittest
from unittest.mock import patch

from true_love_base import configuration

BOTS = 'bots:\n  wxid_m8s:\n    master: "alice"\n  wxid_ser:\n    master: "bob"\n  wxid_quiet: {}\n'


class BotConfigTests(unittest.TestCase):
    def load(self, bots=BOTS, bot_id="wxid_m8s"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with open(os.path.join(directory.name, "config.yaml"), "w", encoding="utf-8") as fp:
            fp.write('http_token: "token"\n' + bots)
        previous = os.getcwd()
        os.chdir(directory.name)
        self.addCleanup(os.chdir, previous)
        with (
            patch.object(configuration.LoggingConfig, "setup") as self.logging_setup,
            patch.object(configuration.Config, "_instance", None),
            patch.object(configuration.Config, "_initialized", False),
            patch.dict(os.environ, {"BOT_ID": bot_id} if bot_id else {}, clear=True),
        ):
            return configuration.Config()

    def test_each_bot_finds_its_own_master(self):
        self.assertEqual(self.load(bot_id="wxid_m8s").master_wix, "alice")
        self.assertEqual(self.load(bot_id="wxid_ser").master_wix, "bob")

    def test_bot_is_known_by_the_injected_id(self):
        self.assertEqual(self.load(bot_id="wxid_ser").bot_id, "wxid_ser")

    def test_logs_are_tagged_with_the_bot(self):
        self.load(bot_id="wxid_ser")

        self.assertEqual(self.logging_setup.call_args.kwargs["loki_tags"], {"bot_id": "wxid_ser"})

    def test_bot_without_a_master_still_starts(self):
        with self.assertLogs("Config", level="WARNING"):
            config = self.load(bot_id="wxid_quiet")

        self.assertEqual(config.master_wix, "")

    def test_base_refuses_to_start_without_a_bot_id(self):
        with self.assertRaisesRegex(SystemExit, "BOT_ID"):
            self.load(bot_id="")

    def test_base_refuses_to_start_for_a_bot_missing_from_the_map(self):
        with self.assertRaisesRegex(SystemExit, "wxid_new"):
            self.load(bot_id="wxid_new")

    def test_file_without_a_bots_map_does_not_start(self):
        with self.assertRaises(SystemExit):
            self.load(bots="")


if __name__ == "__main__":
    unittest.main()
