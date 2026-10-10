"""The words that called the bot are not part of the question; base says which words those were."""

import asyncio
import unittest

from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent.agent_loop import AgentLoop


def question(content, mention=""):
    loop = AgentLoop.__new__(AgentLoop)
    return asyncio.run(loop._build_user_content(ChatMsg(msg_type="text", content=content, mention=mention)))


class TriggerWordTests(unittest.TestCase):
    def test_the_call_reported_by_base_is_left_out_of_the_question(self):
        cases = [
            ("@kun jr 今天天气怎么样", "@kun jr", "今天天气怎么样"),
            ("今天天气怎么样 @真爱粉", "@真爱粉", "今天天气怎么样"),
            ("ZAF 帮我查一下", "ZAF", "帮我查一下"),
        ]
        for content, mention, expected in cases:
            with self.subTest(content=content):
                self.assertEqual(question(content, mention), expected)

    def test_only_the_call_itself_is_removed_not_later_uses_of_the_same_word(self):
        self.assertEqual(question("zaf 你知道 zaf 是什么意思吗", "zaf"), "你知道 zaf 是什么意思吗")

    def test_message_that_called_nobody_is_kept_as_written(self):
        self.assertEqual(question("@张三 你觉得呢"), "@张三 你觉得呢")

    def test_no_bot_name_is_built_into_the_ai(self):
        self.assertEqual(question("@真爱粉 zaf 在吗"), "@真爱粉 zaf 在吗")

    def test_message_that_only_called_the_bot_asks_nothing(self):
        self.assertIsNone(question("@kun jr ", "@kun jr"))


if __name__ == "__main__":
    unittest.main()
