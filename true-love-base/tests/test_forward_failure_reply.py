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
        self.retry_later = Mock()
        dependencies = {
            "true_love_base": module("true_love_base", __path__=[]),
            "true_love_base.core": module("true_love_base.core", WxAutoClient=object),
            "true_love_base.services": module(
                "true_love_base.services", __path__=[],
                server_client=types.SimpleNamespace(get_chat=self.get_chat, retry_later=self.retry_later),
            ),
            "true_love_base.services.private_poller": module(
                "true_love_base.services.private_poller", PrivatePoller=Mock()
            ),
            "true_love_base.models.reply": module("true_love_base.models.reply", REPLY_STYLES=("at",)),
            "true_love_base.services.friend_acceptor": module(
                "true_love_base.services.friend_acceptor", FriendAcceptor=Mock()
            ),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location("forward_failure_robot", SOURCE / "services/robot.py")
        robot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(robot_module)
        self.robot_module = robot_module

        self.client = Mock()
        self.client.send_text.return_value = True
        self.robot = robot_module.Robot(self.client, Mock())
        self.addCleanup(self.robot.cleanup)

    def test_private_message_gets_error_reply_when_server_rejects(self):
        self.get_chat.return_value = "server down"

        self.robot._process_message(ChatMsg(sender_id="alice"), "alice")

        self.client.send_text.assert_called_once_with("alice", "server down", None)

    def test_message_the_user_was_told_about_is_resent_for_the_archive_only(self):
        self.get_chat.return_value = "server down"
        msg = ChatMsg(sender_id="alice")

        self.robot._process_message(msg, "alice")

        self.retry_later.assert_called_once_with(msg, archive_only=True)

    def test_message_whose_failure_reply_was_lost_is_resent_in_full(self):
        self.get_chat.return_value = "server down"
        self.client.send_text.return_value = False
        msg = ChatMsg(sender_id="alice", msg_hash="h1")

        with self.assertLogs("Robot", level="WARNING") as logs:
            self.robot._process_message(msg, "alice")

        self.retry_later.assert_called_once_with(msg, archive_only=False)
        self.assertIn("h1", logs.output[-1])

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
        self.retry_later.assert_called_once_with(msg, archive_only=False)

    def test_accepted_message_sends_nothing_locally(self):
        self.robot._process_message(ChatMsg(sender_id="alice"), "alice")

        self.client.send_text.assert_not_called()
        self.retry_later.assert_not_called()

    def test_processing_error_is_logged_with_its_stack(self):
        self.get_chat.side_effect = RuntimeError("boom")

        with self.assertLogs("Robot", level="ERROR") as logs:
            self.robot._process_message(ChatMsg(sender_id="alice", msg_hash="h1"), "alice")

        self.assertIsNotNone(logs.records[-1].exc_info)
        self.assertIn("h1", logs.output[-1])

    def test_message_that_cannot_be_queued_is_logged_with_its_stack(self):
        self.robot._executor = Mock(**{"submit.side_effect": RuntimeError("pool broken")})

        with self.assertLogs("Robot", level="ERROR") as logs:
            self.robot.on_message(ChatMsg(sender_id="alice", msg_hash="h1"), "alice")

        self.assertIsNotNone(logs.records[-1].exc_info)
        self.assertIn("h1", logs.output[-1])

    def test_master_is_told_once_the_server_is_down(self):
        self.robot._master = "owner"

        self.robot.announce_server_down()

        self.assertEqual(self.client.send_text.call_args.args[0], "owner")

    def test_server_down_without_a_master_is_a_warning(self):
        robot = self.robot_module.Robot(self.client, "")
        self.addCleanup(robot.cleanup)

        with self.assertLogs("Robot", level="WARNING"):
            robot.announce_server_down()

        self.client.send_text.assert_not_called()

    def test_accepted_friends_without_a_master_are_logged_as_a_warning(self):
        robot = self.robot_module.Robot(self.client, "")
        self.addCleanup(robot.cleanup)

        with self.assertLogs("Robot", level="WARNING") as logs:
            robot._announce_new_friends(["alice hi"])

        self.assertIn("alice hi", logs.output[-1])
        self.client.send_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
