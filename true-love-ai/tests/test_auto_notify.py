"""Skill notices ("正在…") go out when someone asked the bot, not when it joins in on its own."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import agent_loop, server_client


class AutoNotifyTests(unittest.IsolatedAsyncioTestCase):
    async def notices(self, **fields):
        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        tool_call = [{"id": "c1", "name": "analyze_image", "arguments": {}}]
        loop.llm_router = types.SimpleNamespace(
            chat_for_agent=AsyncMock(side_effect=[("tool_calls", tool_call), ("text", "图上是一只猫")]))
        session = types.SimpleNamespace(add_message=lambda *a: None, get_messages_for_llm=lambda access: [])
        loop.session_manager = types.SimpleNamespace(get_or_create=lambda *a, **k: session)
        loop._send_reply = AsyncMock()
        msg = ChatMsg(bot_id="bot_a", platform="wechat", chat_id="room", sender_id="alice", content="hi", **fields)
        with patch.object(agent_loop, "get_user_context", return_value=None), \
                patch.object(agent_loop.skill_registry, "get_all_tool_schemas", return_value=[]), \
                patch.object(agent_loop.skill_registry, "get_notify", return_value="让我仔细看看这张图片"), \
                patch.object(agent_loop.skill_registry, "execute", AsyncMock(return_value="一只猫")), \
                patch.object(server_client, "send_text", AsyncMock(return_value=True)) as send:
            await loop.run(msg)
        loop._send_reply.assert_awaited()
        return [call.args[1] for call in send.await_args_list]

    async def test_auto_triggered_group_message_gets_no_notice(self):
        self.assertEqual(await self.notices(is_group=True, is_at_me=False), [])

    async def test_mention_in_a_group_gets_the_notice(self):
        self.assertEqual(await self.notices(is_group=True, is_at_me=True), ["让我仔细看看这张图片"])

    async def test_private_chat_gets_the_notice(self):
        self.assertEqual(await self.notices(is_group=False), ["让我仔细看看这张图片"])


if __name__ == "__main__":
    unittest.main()
