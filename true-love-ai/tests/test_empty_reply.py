"""When the model says nothing after running a skill, the user still gets an answer."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import agent_loop


class EmptyReplyTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, answers):
        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        loop.llm_router = types.SimpleNamespace(chat_for_agent=AsyncMock(side_effect=answers))
        session = types.SimpleNamespace(add_message=lambda *a: None, get_messages_for_llm=lambda access: [])
        loop.session_manager = types.SimpleNamespace(get_or_create=lambda *a, **k: session)
        loop._send_reply = AsyncMock()
        msg = ChatMsg(bot_id="bot_a", platform="wechat", chat_id="room", sender_id="alice", is_group=True, content="hi")
        with patch.object(agent_loop, "get_user_context", return_value=None), \
                patch.object(agent_loop.skill_registry, "get_all_tool_schemas", return_value=[]), \
                patch.object(agent_loop.skill_registry, "get_notify", return_value=None), \
                patch.object(agent_loop.skill_registry, "execute", AsyncMock(return_value="时区已经记下啦")):
            with self.assertLogs("AgentLoop", level="WARNING"):
                await loop.run(msg)
        return loop._send_reply.await_args.args[1]

    async def test_the_skill_result_is_sent_when_the_model_replies_empty(self):
        tool_call = [{"id": "c1", "name": "save_user_profile", "arguments": {}}]
        self.assertEqual(await self.run_loop([("tool_calls", tool_call), ("text", "")]), "时区已经记下啦")

    async def test_an_empty_first_reply_still_gets_an_answer(self):
        self.assertTrue(await self.run_loop([("text", "  ")]))


if __name__ == "__main__":
    unittest.main()
