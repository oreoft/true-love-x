"""Base only reports a tailnet address as its callback."""

import unittest
from unittest.mock import MagicMock, patch

from true_love_base.utils import tailnet


def local_address(ip=None, error=None):
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.connect.side_effect = error
    probe.getsockname.return_value = (ip, 50000)
    return patch.object(tailnet.socket, "socket", return_value=probe)


class TailnetTests(unittest.TestCase):
    def test_tailnet_address_is_used(self):
        with local_address("100.67.129.48"):
            self.assertEqual(tailnet.tailnet_ip(), "100.67.129.48")

    def test_lan_address_is_refused(self):
        with local_address("192.168.1.8"), self.assertRaisesRegex(RuntimeError, "192.168.1.8"):
            tailnet.tailnet_ip()

    def test_no_route_is_refused(self):
        with local_address(error=OSError("Network is unreachable")), self.assertRaisesRegex(RuntimeError, "tailnet"):
            tailnet.tailnet_ip()


if __name__ == "__main__":
    unittest.main()
