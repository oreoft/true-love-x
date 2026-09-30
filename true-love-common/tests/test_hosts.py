"""Every bot shares one server and one AI; only base addresses are dynamic, reported by base itself."""

import unittest
from unittest.mock import patch

from true_love_common import hosts


class HostsTests(unittest.TestCase):
    def env(self, **values):
        patcher = patch.dict("os.environ", values, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_production_uses_the_shared_server_and_ai(self):
        self.env(APP_ENV="prod")

        self.assertEqual(hosts.server_host(), hosts.SERVER_HOST)
        self.assertEqual(hosts.ai_host(), hosts.AI_HOST)

    def test_development_talks_to_the_local_server_and_ai(self):
        self.env()

        self.assertEqual(hosts.server_host(), hosts.DEV_SERVER_HOST)
        self.assertEqual(hosts.ai_host(), hosts.DEV_AI_HOST)


if __name__ == "__main__":
    unittest.main()
