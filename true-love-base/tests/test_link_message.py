"""A link whose url the SDK cannot read is still the group message it was, not a private chat."""

import types
import unittest

from true_love_base.models.message_converter import convert_message


def link(get_url):
    return types.SimpleNamespace(
        type="link", attr="friend", content="[链接]中国男足今天打老对手", sender="alice", id="id-1", hash="hash-1",
        chat_info={"chat_type": "group", "chat_name": "room"}, get_url=get_url,
    )


class FailedResponse(dict):
    """SDK 的 WxResponse：dict 子类，构造要两个参数，失败时为假"""

    def __init__(self, status, message, data=None):
        super().__init__(status=status, message=message, data=data)

    def __bool__(self):
        return self["status"] == "成功"


def timeout():
    raise TimeoutError("Find Control Timeout: {ClassName: 'AppMenuButton'}")


class LinkMessageTests(unittest.TestCase):
    def test_link_keeps_its_url(self):
        msg = convert_message(link(lambda: "https://mp.weixin.qq.com/s/x"), "room", bot_name="kun jr")

        self.assertEqual(msg.msg_type, "link")
        self.assertEqual(msg.link_msg.url, "https://mp.weixin.qq.com/s/x")

    def test_link_whose_url_times_out_stays_a_group_link_without_url(self):
        msg = convert_message(link(timeout), "room", bot_name="kun jr")

        self.assertEqual(msg.msg_type, "link")
        self.assertTrue(msg.is_group)
        self.assertFalse(msg.is_at_me)
        self.assertEqual(msg.sender_id, "alice")
        self.assertEqual(msg.msg_hash, "hash-1")
        self.assertIsNone(msg.link_msg.url)

    def test_link_whose_url_comes_back_as_a_failed_response_can_still_be_sent(self):
        from true_love_base.models.api import ChatRequest
        from true_love_common.bot import BotInfo

        with self.assertLogs("MessageConverter", level="WARNING") as logs:
            msg = convert_message(link(lambda: FailedResponse("失败", "获取链接超时")), "room", bot_name="kun jr")

        self.assertIsNone(msg.link_msg.url)
        self.assertIn("获取链接超时", "".join(logs.output))
        bot = BotInfo(bot_id="bot", platform="wechat", callback="", name="")
        self.assertIn('"msg_hash": "hash-1"', ChatRequest("t", bot, msg).to_json())


if __name__ == "__main__":
    unittest.main()
