"""Exercise real send_text behavior with only the Windows SDK boundary isolated."""

import contextlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = (
    Path(__file__).parents[1] / "src/true_love_base/core/wxauto_client.py"
)


def load_client_class():
    dependencies = {}
    for name, attributes in {
        "true_love_base.wxautox4x.wxautox4x": {"WeChat": object},
        "wxautox4.param": {"WxParam": type("WxParam", (), {})},
        "wxautox4.uia.uiautomation": {"InitializeUIAutomationInCurrentThread": lambda: None},
        "wxautox4.utils.lock": {"ui_transaction": lambda timeout=30.0: contextlib.nullcontext()},
        "true_love_common.chat_msg": {"ChatMsg": object},
        "true_love_base.models.message_converter": {"convert_message": None},
        "true_love_base.utils.path_resolver": {"get_wx_imgs_dir": lambda: None},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        dependencies[name] = module
    spec = importlib.util.spec_from_file_location("native_mentions_subject", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module.WxAutoClient


class NativeMentionsTests(unittest.TestCase):
    def setUp(self):
        client_class = load_client_class()
        self.client = client_class.__new__(client_class)
        self.sdk = Mock()
        self.sdk.GetSubWindow.return_value = None
        self.sdk.SendMsg.return_value = True
        self.client._wx = self.sdk

    def test_main_window_receives_native_mentions_and_unchanged_body(self):
        self.assertTrue(self.client.send_text("研发群", "正文\n第二行", ["张三", "李四"]))

        self.sdk.GetSubWindow.assert_called_once_with("研发群")
        self.sdk.SendMsg.assert_called_once_with("正文\n第二行", "研发群", at=["张三", "李四"])

    def test_existing_subwindow_receives_native_mentions_without_switching_main(self):
        subwindow = Mock()
        subwindow.SendMsg.return_value = True
        self.sdk.GetSubWindow.return_value = subwindow

        self.assertTrue(self.client.send_text("研发群", "  原样正文  ", ["张三"]))

        subwindow.SendMsg.assert_called_once_with("  原样正文  ", at=["张三"])
        self.sdk.SendMsg.assert_not_called()

    def test_messages_without_mentions_keep_body_unchanged_on_both_paths(self):
        for has_subwindow in (False, True):
            for at_list in (None, []):
                with self.subTest(subwindow=has_subwindow, at_list=at_list):
                    self.sdk.reset_mock()
                    subwindow = Mock() if has_subwindow else None
                    self.sdk.GetSubWindow.return_value = subwindow
                    sender = subwindow if has_subwindow else self.sdk
                    sender.SendMsg.return_value = True

                    self.assertTrue(self.client.send_text("好友", "@普通文本\n正文", at_list))

                    expected_args = ("@普通文本\n正文",) if has_subwindow else ("@普通文本\n正文", "好友")
                    sender.SendMsg.assert_called_once_with(*expected_args, at=at_list)
                    if has_subwindow:
                        self.sdk.SendMsg.assert_not_called()

    def test_sdk_failure_stays_false_on_both_paths(self):
        for has_subwindow in (False, True):
            with self.subTest(subwindow=has_subwindow):
                subwindow = Mock() if has_subwindow else None
                self.sdk.GetSubWindow.return_value = subwindow
                sender = subwindow if has_subwindow else self.sdk
                sender.SendMsg.return_value = False

                with self.assertLogs("WxAutoClient", level="ERROR"):
                    self.assertFalse(self.client.send_text("研发群", "正文", ["张三"]))

    def test_sdk_exception_stays_false(self):
        self.sdk.SendMsg.side_effect = RuntimeError("window closed")

        with self.assertLogs("WxAutoClient", level="ERROR"):
            self.assertFalse(self.client.send_text("研发群", "正文", ["张三"]))


if __name__ == "__main__":
    unittest.main()
