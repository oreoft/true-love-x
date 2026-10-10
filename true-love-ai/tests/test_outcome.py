"""Every message ends in one named outcome; what gets sent follows from it, and auto replies stay quiet unless they say something."""

import unittest

from true_love_ai.agent import outcome
from true_love_ai.agent.outcome import Ending, Outcome


class TextToSendTests(unittest.TestCase):
    def test_what_each_outcome_sends_when_someone_asked(self):
        self.assertEqual(outcome.text_to_send(Ending(Outcome.REPLIED, "好耶"), auto=False), "好耶")
        self.assertIsNone(outcome.text_to_send(Ending(Outcome.SKIPPED), auto=False))
        self.assertEqual(outcome.text_to_send(Ending(Outcome.EMPTY_FALLBACK, "技能结果"), auto=False), "技能结果")
        for kind in (Outcome.EMPTY_FALLBACK, Outcome.LLM_ERROR, Outcome.TOO_MANY_ROUNDS, Outcome.UNREADABLE,
                     Outcome.CRASHED):
            with self.subTest(kind=kind):
                self.assertEqual(outcome.text_to_send(Ending(kind), auto=False), outcome.FALLBACK_TEXT[kind])

    def test_auto_replies_send_only_a_real_answer(self):
        self.assertEqual(outcome.text_to_send(Ending(Outcome.REPLIED, "这哥们练得不错"), auto=True), "这哥们练得不错")
        for kind in Outcome:
            if kind is not Outcome.REPLIED:
                with self.subTest(kind=kind):
                    self.assertIsNone(outcome.text_to_send(Ending(kind, "技能结果"), auto=True))

    def test_skip_marker_only_counts_on_auto_replies(self):
        self.assertIs(outcome.from_model_reply(" [不回复] ", "", auto=True).outcome, Outcome.SKIPPED)
        self.assertIs(outcome.from_model_reply("[不回复]", "", auto=False).outcome, Outcome.REPLIED)
        self.assertIs(outcome.from_model_reply("  ", "", auto=True).outcome, Outcome.EMPTY_FALLBACK)


class FailedSkillOutcomeTests(unittest.TestCase):
    def test_an_auto_reply_that_chose_to_stay_quiet_is_skipped_even_after_a_failed_skill(self):
        ending = outcome.from_model_reply("[不回复]", "", auto=True, failed_tools=["analyze_image"])

        self.assertIs(ending.outcome, Outcome.SKIPPED)

    def test_no_unrecovered_failure_means_a_normal_reply(self):
        ending = outcome.from_model_reply("哈哈", "一只猫", auto=True, failed_tools=[])

        self.assertIs(ending.outcome, Outcome.REPLIED)
        self.assertEqual(outcome.text_to_send(ending, auto=True), "哈哈")


if __name__ == "__main__":
    unittest.main()
