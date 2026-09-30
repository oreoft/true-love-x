"""UI automation needs an awake desktop at 100% display scaling."""

import ctypes
import sys
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from true_love_base.utils import win_env


class DisplayScaleTests(unittest.TestCase):
    def setUp(self):
        self.registry = types.ModuleType("winreg")
        self.registry.HKEY_CURRENT_USER = "HKCU"
        self.registry.OpenKey = MagicMock()
        self.registry.QueryValueEx = Mock()

    def read_scale(self, platform="win32", monitor_dpi=None):
        with patch.object(sys, "platform", platform), patch.dict(sys.modules, {"winreg": self.registry}), \
                patch.object(win_env, "_monitor_dpi", Mock(return_value=monitor_dpi)):
            return win_env.display_scale_percent()

    def test_current_monitor_scaling_wins_over_the_value_applied_at_sign_in(self):
        self.registry.QueryValueEx.return_value = (216, 4)

        self.assertEqual(self.read_scale(monitor_dpi=192), 200)
        self.registry.OpenKey.assert_not_called()

    def test_monitor_that_cannot_be_read_falls_back_to_the_registry(self):
        self.registry.QueryValueEx.return_value = (216, 4)

        with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"winreg": self.registry}), \
                patch.object(win_env, "_monitor_dpi", Mock(side_effect=OSError("no shcore"))):
            self.assertEqual(win_env.display_scale_percent(), 225)

    def test_scaling_is_reported_as_a_percentage(self):
        for dpi, percent in [(96, 100), (120, 125), (144, 150), (168, 175), (192, 200)]:
            with self.subTest(dpi=dpi):
                self.registry.QueryValueEx.return_value = (dpi, 4)

                self.assertEqual(self.read_scale(), percent)

    def test_scaling_comes_from_the_dpi_applied_to_the_current_user(self):
        self.registry.QueryValueEx.return_value = (96, 4)

        self.read_scale()

        self.registry.OpenKey.assert_called_once_with("HKCU", r"Control Panel\Desktop\WindowMetrics")
        key = self.registry.OpenKey.return_value.__enter__.return_value
        self.registry.QueryValueEx.assert_called_once_with(key, "AppliedDPI")

    def test_scaling_is_unknown_when_the_registry_has_no_value(self):
        self.registry.QueryValueEx.side_effect = FileNotFoundError("AppliedDPI")

        self.assertIsNone(self.read_scale())

    def test_scaling_is_unknown_off_windows(self):
        self.assertIsNone(self.read_scale(platform="linux"))

        self.registry.OpenKey.assert_not_called()


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
