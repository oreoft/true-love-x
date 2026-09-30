"""UI automation needs an awake desktop."""

import ctypes
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from true_love_base.utils import win_env


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        self.kernel32 = Mock()
        self.kernel32.SetThreadExecutionState.return_value = 0x80000000

    def keep_awake(self, platform="win32"):
        windll = Mock(kernel32=self.kernel32)
        with patch.object(sys, "platform", platform), patch.object(ctypes, "windll", windll, create=True):
            win_env.keep_awake()

    def test_system_sleep_and_display_off_are_blocked_until_base_exits(self):
        self.keep_awake()

        self.kernel32.SetThreadExecutionState.assert_called_once_with(0x80000003)

    def test_base_keeps_starting_when_windows_rejects_the_request(self):
        self.kernel32.SetThreadExecutionState.return_value = 0

        with self.assertLogs("WinEnv", level="WARNING"):
            self.keep_awake()

    def test_base_keeps_starting_when_the_call_fails(self):
        self.kernel32.SetThreadExecutionState.side_effect = OSError("access denied")

        with self.assertLogs("WinEnv", level="WARNING"):
            self.keep_awake()

    def test_nothing_is_requested_off_windows(self):
        self.keep_awake(platform="linux")

        self.kernel32.SetThreadExecutionState.assert_not_called()


if __name__ == "__main__":
    unittest.main()
