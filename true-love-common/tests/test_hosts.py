"""Every service finds the others in one shared table keyed by bot, the machine name of its base."""

import unittest
from unittest.mock import patch

from true_love_common import hosts


class HostsTests(unittest.TestCase):
    def env(self, **values):
        patcher = patch.dict("os.environ", values, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bot_is_found_in_any_letter_case(self):
        self.assertEqual(hosts.bot_hosts("WIN10-M8S"), hosts.BOTS["win10-m8s"])

    def test_unknown_bot_says_where_to_register_it(self):
        with self.assertRaisesRegex(KeyError, "hosts.py"):
            hosts.bot_hosts("win12-new")

    def test_server_in_a_container_learns_its_bot_from_the_environment(self):
        self.env(APP_ENV="prod", BOT_ID="WIN11-SER")

        self.assertEqual(hosts.machine_bot_id(), "win11-ser")

    def test_production_uses_the_registered_addresses(self):
        self.env(APP_ENV="prod")

        self.assertEqual(hosts.server_host("win11-ser"), hosts.BOTS["win11-ser"].server)
        self.assertEqual(hosts.ai_host(), hosts.AI_HOST)

    def test_development_talks_to_the_local_server_and_ai(self):
        self.env()

        self.assertEqual(hosts.machine_bot_id(), hosts.DEV_BOT_ID)
        self.assertEqual(hosts.server_host("win11-ser"), hosts.DEV_SERVER_HOST)
        self.assertEqual(hosts.ai_host(), hosts.DEV_AI_HOST)

    def test_bot_for_ai_notices_is_registered(self):
        self.assertIn(hosts.AI_NOTICE_BOT, hosts.BOTS)


if __name__ == "__main__":
    unittest.main()
