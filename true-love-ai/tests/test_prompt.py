"""What the model is told: the stable part, the per-message header, and what is kept in history."""

import unittest
from datetime import datetime, timezone

from true_love_ai.agent import prompt

NOON_UTC = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)


class PromptTests(unittest.TestCase):
    def test_group_messages_are_stored_with_the_speaker(self):
        self.assertEqual(prompt.stored_text("在吗", "张三", is_group=True), "张三：在吗")
        self.assertEqual(prompt.stored_text("在吗", "张三", is_group=False), "在吗")

    def test_time_is_given_in_the_users_own_timezone(self):
        text = prompt.time_context("职业：程序员 | 时区：America/Chicago", now=NOON_UTC)

        self.assertIn("(UTC) 2026-04-13 12:00:00", text)
        self.assertIn("时区=America/Chicago) 2026-04-13 07:00:00", text)

    def test_unknown_or_missing_timezone_falls_back_to_beijing(self):
        for ctx in (None, "", "时区：Mars/Olympus", "职业：程序员"):
            with self.subTest(ctx=ctx):
                self.assertEqual(prompt.user_timezone(ctx), "Asia/Shanghai")

    def test_live_prompt_has_time_sender_words_and_the_auto_rule_only_when_auto(self):
        live = prompt.live_prompt("张三：在吗", sender_name="张三", user_ctx="职业：程序员", auto=False, now=NOON_UTC)
        auto = prompt.live_prompt("张三：在吗", sender_name="张三", user_ctx=None, auto=True, now=NOON_UTC)

        self.assertTrue(live.startswith("【当前时间】"))
        self.assertIn("【发送者】张三，已知信息：职业：程序员", live)
        self.assertIn("\n张三：在吗", live)
        self.assertNotIn(prompt.AUTO_REPLY_RULE, live)
        self.assertTrue(auto.endswith(prompt.AUTO_REPLY_RULE))
        self.assertIn("【发送者】张三\n", auto)

    def test_instructions_only_hold_what_rarely_changes(self):
        text = prompt.instructions("你是小助手。", summary="【摘要】")

        self.assertTrue(text.startswith("你是小助手。"))
        self.assertIn(prompt.FORMAT_RULE, text)
        self.assertIn("fetch_group_context", text)
        self.assertTrue(text.endswith("【摘要】"))
        self.assertNotIn("早期对话摘要", prompt.instructions("你是小助手。"))


if __name__ == "__main__":
    unittest.main()
