"""Every message ends in one named outcome; what gets sent follows from it, and auto replies stay quiet unless they say something."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import agent_loop, outcome
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


class AutoReplyTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, answer, **fields):
        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        chat = AsyncMock(return_value=("text", answer))
        loop.llm_router = types.SimpleNamespace(chat_for_agent=chat)
        history = []
        session = types.SimpleNamespace(add_message=lambda role, text: history.append((role, text)),
                                        get_messages_for_llm=lambda access: [{"role": "user", "content": "x"}])
        loop.session_manager = types.SimpleNamespace(get_or_create=lambda *a, **k: session)
        loop._send_reply = AsyncMock()
        msg = ChatMsg(bot_id="bot_a", platform="wechat", chat_id="room", sender_id="alice", msg_id="m1",
                      content="看这个", **fields)
        with patch.object(agent_loop, "get_user_context", return_value=None), \
                patch.object(agent_loop.skill_registry, "get_all_tool_schemas", return_value=[]):
            with self.assertLogs("Outcome", level="INFO") as logs:
                await loop.run(msg)
        prompt = chat.await_args.kwargs["messages"]
        return loop._send_reply, history, prompt, logs.output[-1]

    async def test_auto_reply_with_nothing_to_say_is_not_sent(self):
        send, history, prompt, log = await self.run_loop("[不回复]", is_group=True)

        send.assert_not_awaited()
        self.assertEqual(history, [("user", "看这个")])
        self.assertIn(agent_loop.AUTO_REPLY_RULE, prompt[-1]["content"])
        self.assertIn("outcome=skipped", log)

    async def test_auto_reply_with_something_to_say_is_sent(self):
        send, history, _, log = await self.run_loop("这哥们深蹲姿势不太对", is_group=True)

        send.assert_awaited_once_with("room", "这哥们深蹲姿势不太对", "alice", "m1")
        self.assertEqual(history[-1], ("assistant", "这哥们深蹲姿势不太对"))
        self.assertIn("outcome=replied", log)

    async def test_mention_does_not_get_the_auto_rule(self):
        _, _, prompt, _ = await self.run_loop("好的", is_group=True, is_at_me=True)

        self.assertNotIn(agent_loop.AUTO_REPLY_RULE, str(prompt))

    async def test_llm_error_on_an_auto_reply_stays_quiet(self):
        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        loop.llm_router = types.SimpleNamespace(chat_for_agent=AsyncMock(side_effect=RuntimeError("down")))
        session = types.SimpleNamespace(add_message=lambda *a: None, get_messages_for_llm=lambda access: [])
        loop.session_manager = types.SimpleNamespace(get_or_create=lambda *a, **k: session)
        loop._send_reply = AsyncMock()
        msg = ChatMsg(bot_id="bot_a", chat_id="room", sender_id="alice", is_group=True, content="看这个")
        with patch.object(agent_loop, "get_user_context", return_value=None), \
                patch.object(agent_loop.skill_registry, "get_all_tool_schemas", return_value=[]), \
                self.assertLogs("Outcome", level="WARNING") as logs, self.assertLogs("AgentLoop", level="ERROR"):
            await loop.run(msg)

        loop._send_reply.assert_not_awaited()
        self.assertIn("outcome=llm_error sent=False auto=True", logs.output[-1])


if __name__ == "__main__":
    unittest.main()
