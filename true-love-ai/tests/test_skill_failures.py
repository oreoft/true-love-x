"""A skill that fails ends the message as tool_failed, not replied (the agent side is in test_agent_loop)."""

import unittest

from true_love_ai.agent import outcome
from true_love_ai.agent.outcome import Ending, Outcome


class ToolFailedOutcomeTests(unittest.TestCase):
    def test_a_failed_skill_wins_over_a_normal_reply(self):
        ending = outcome.from_model_reply("画失败了，晚点再试吧", "呜呜~", auto=False, failed_tools=["generate_image"])

        self.assertIs(ending.outcome, Outcome.TOOL_FAILED)
        self.assertEqual(ending.detail, "generate_image")
        self.assertEqual(outcome.text_to_send(ending, auto=False), "画失败了，晚点再试吧")

    def test_an_empty_reply_after_a_failure_sends_what_the_skill_said(self):
        ending = outcome.from_model_reply("", "呜呜~图片生成失败了", auto=False, failed_tools=["generate_image"])

        self.assertEqual(outcome.text_to_send(ending, auto=False), "呜呜~图片生成失败了")
        self.assertEqual(outcome.text_to_send(Ending(Outcome.TOOL_FAILED), auto=False),
                         outcome.FALLBACK_TEXT[Outcome.TOOL_FAILED])

    def test_auto_replies_stay_quiet_after_a_failure(self):
        ending = outcome.from_model_reply("说点啥", "", auto=True, failed_tools=["analyze_image"])

        self.assertIsNone(outcome.text_to_send(ending, auto=True))


if __name__ == "__main__":
    unittest.main()
