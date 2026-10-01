"""A skill that fails is logged with its stack and the message ends as tool_failed, not replied."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import agent_loop, outcome
from true_love_ai.agent.outcome import Ending, Outcome
from true_love_ai.agent.skill_registry import SkillFailed
from true_love_ai.llm import router


def tool_call(name="generate_image", **extra):
    return {"id": "c1", "name": name, "arguments": {"prompt": "猫"}, **extra}


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


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, execute, calls):
        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        answers = [("tool_calls", calls), ("text", "画失败了，晚点再试吧")]
        loop.llm_router = types.SimpleNamespace(chat_for_agent=AsyncMock(side_effect=answers))
        session = types.SimpleNamespace(add_message=lambda *a: None, get_messages_for_llm=lambda access: [])
        loop.session_manager = types.SimpleNamespace(get_or_create=lambda *a, **k: session)
        loop._send_reply = AsyncMock(return_value=True)
        msg = ChatMsg(bot_id="bot_a", platform="wechat", chat_id="room", sender_id="alice", is_group=True,
                      is_at_me=True, content="画只猫")
        with patch.object(agent_loop, "get_user_context", return_value=None), \
                patch.object(agent_loop.skill_registry, "get_all_tool_schemas", return_value=[]), \
                patch.object(agent_loop.skill_registry, "get_notify", return_value=None), \
                patch.object(agent_loop.skill_registry, "execute", execute), \
                self.assertLogs("Outcome", level="INFO") as outcome_logs:
            await loop.run(msg)
        prompt = loop.llm_router.chat_for_agent.await_args_list[-1].kwargs["messages"]
        return loop._send_reply, outcome_logs.output[-1], prompt

    async def test_skill_failure_is_logged_with_its_cause_and_counted_as_failed(self):
        async def fail(name, params, ctx):
            try:
                raise TimeoutError("upstream timed out")
            except TimeoutError as e:
                raise SkillFailed("呜呜~图片生成出错了捏") from e

        with self.assertLogs("AgentLoop", level="ERROR") as loop_logs:
            send, outcome_log, prompt = await self.run_loop(fail, [tool_call()])

        self.assertIn("outcome=tool_failed", outcome_log)
        self.assertIn("detail=generate_image", outcome_log)
        error = next(r for r in loop_logs.records if r.levelname == "ERROR")
        self.assertIsNotNone(error.exc_info)
        self.assertEqual(prompt[-1]["content"], "呜呜~图片生成出错了捏")
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[1], "画失败了，晚点再试吧")

    async def test_unexpected_exception_is_also_counted_as_failed(self):
        with self.assertLogs("AgentLoop", level="ERROR"):
            _, outcome_log, prompt = await self.run_loop(AsyncMock(side_effect=RuntimeError("boom")), [tool_call()])

        self.assertIn("outcome=tool_failed", outcome_log)
        self.assertEqual(prompt[-1]["content"], "[执行失败] boom")

    async def test_bad_arguments_are_sent_back_to_the_model_instead_of_running_with_nothing(self):
        execute = AsyncMock(return_value="不该执行")
        _, outcome_log, prompt = await self.run_loop(execute, [tool_call(arguments_error="参数不是合法的 JSON")])

        execute.assert_not_awaited()
        self.assertIn("[参数错误]", prompt[-1]["content"])
        self.assertIn("outcome=replied", outcome_log)

    async def test_a_reply_that_did_not_go_out_is_logged_as_not_sent(self):
        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        loop._send_reply = AsyncMock(return_value=False)
        msg = ChatMsg(bot_id="bot_a", chat_id="alice", sender_id="alice", content="hi")
        with self.assertLogs("Outcome", level="INFO") as logs:
            await loop.finish(msg, Ending(Outcome.REPLIED, "好耶"))

        self.assertIn("outcome=replied sent=False", logs.output[-1])


class ToolArgumentsTests(unittest.TestCase):
    @staticmethod
    def call(arguments):
        return types.SimpleNamespace(id="c1", function=types.SimpleNamespace(name="set_reminder", arguments=arguments))

    def test_valid_arguments_are_parsed(self):
        self.assertEqual(router._parse_tool_call(self.call('{"content": "关火"}'))["arguments"], {"content": "关火"})
        self.assertNotIn("arguments_error", router._parse_tool_call(self.call("")))

    def test_broken_json_is_reported_with_the_raw_text(self):
        with self.assertLogs("LLMRouter", level="WARNING") as logs:
            call = router._parse_tool_call(self.call('{"content": "关'))

        self.assertEqual(call["arguments"], {})
        self.assertIn("JSON", call["arguments_error"])
        self.assertIn('{"content": "关', logs.output[0])

    def test_arguments_that_are_not_an_object_are_rejected(self):
        with self.assertLogs("LLMRouter", level="WARNING"):
            self.assertIn("arguments_error", router._parse_tool_call(self.call("[1, 2]")))


if __name__ == "__main__":
    unittest.main()
