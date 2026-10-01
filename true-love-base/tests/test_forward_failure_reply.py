"""A message that expects a reply must not go silent when the server rejects it."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from true_love_common.chat_msg import ChatMsg


SOURCE = Path(__file__).parents[1] / "src/true_love_base"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class ForwardFailureReplyTests(unittest.TestCase):
    def setUp(self):
        self.get_chat = Mock(return_value="")
        dependencies = {
            "true_love_base": module("true_love_base", __path__=[]),
            "true_love_base.core": module("true_love_base.core", WxAutoClient=object),
            "true_love_base.services": module(
                "true_love_base.services", __path__=[],
                server_client=types.SimpleNamespace(get_chat=self.get_chat),
            ),
            "true_love_base.services.private_poller": module(
                "true_love_base.services.private_poller", PrivatePoller=Mock()
            ),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location("forward_failure_robot", SOURCE / "services/robot.py")
        robot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(robot_module)

        self.client = Mock()
        self.client.send_text.return_value = True
        self.robot = robot_module.Robot(self.client, Mock())
        self.addCleanup(self.robot.cleanup)

    def test_private_message_gets_error_reply_when_server_rejects(self):
        self.get_chat.return_value = "server down"

        self.robot._process_message(ChatMsg(sender_id="alice"), "alice")

        self.client.send_text.assert_called_once_with("alice", "server down", None)

    def test_group_mention_gets_error_reply_that_mentions_sender(self):
        self.get_chat.return_value = "server down"
        msg = ChatMsg(sender_id="alice", chat_id="room", is_group=True, is_at_me=True)

        self.robot._process_message(msg, "room")

        self.client.send_text.assert_called_once_with("room", "server down", ["alice"])

    def test_plain_group_message_stays_silent_when_server_rejects(self):
        self.get_chat.return_value = "server down"
        msg = ChatMsg(sender_id="alice", chat_id="room", is_group=True)

        self.robot._process_message(msg, "room")

        self.client.send_text.assert_not_called()

    def test_accepted_message_sends_nothing_locally(self):
        self.robot._process_message(ChatMsg(sender_id="alice"), "alice")

        self.client.send_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
