"""Conversations belong to a bot; prompts are still chosen by platform and chat."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import agent_loop, server_client
from true_love_ai.core.session import SessionManager


class PromptTests(unittest.TestCase):
    def manager(self, user_prompt_map):
        manager = SessionManager.__new__(SessionManager)
        manager.default_prompt = "default"
        manager.prompts = {"lark": "lark prompt", "vip": "vip prompt"}
        manager.user_prompt_map = user_prompt_map
        return manager

    def test_prompt_is_chosen_by_platform_and_chat(self):
        manager = self.manager({"wechat:群A": "vip", "lark:*": "lark"})

        self.assertEqual(manager._resolve_prompt("wechat:群A"), "vip prompt")
        self.assertEqual(manager._resolve_prompt("lark:anyone"), "lark prompt")
        self.assertEqual(manager._resolve_prompt("wechat:群B"), "default")


class Stop(Exception):
    """Ends the agent loop right after it picks the conversation"""


class SessionKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_group_on_two_bots_is_two_conversations(self):
        created = []

        def get_or_create(session_id, user_ctx=None, prompt_key=None):
            created.append((session_id, prompt_key))
            raise Stop()

        loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
        loop.session_manager = types.SimpleNamespace(get_or_create=get_or_create)

        for bot_id in ("wxid_m8s", "wxid_ser"):
            msg = ChatMsg(bot_id=bot_id, platform="wechat", chat_id="群A", sender_id="alice", is_group=True,
                          content="hi")
            with patch.object(agent_loop, "get_user_context", return_value=None) as user_ctx:
                with self.assertRaises(Stop):
                    await loop.run(msg)
            user_ctx.assert_called_once_with(f"{bot_id}:群A", "alice")

        self.assertEqual(created, [("wxid_m8s:群A", "wechat:群A"), ("wxid_ser:群A", "wechat:群A")])


class ServerClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_asked_for_the_current_bot(self):
        post = AsyncMock(return_value=types.SimpleNamespace(ok=True, data={"code": 0, "data": {"messages": []}}))
        config = types.SimpleNamespace(http=types.SimpleNamespace(token=["token"]))
        with patch.object(server_client, "async_post_json", post), \
                patch.object(server_client, "get_config", return_value=config), \
                patch.object(server_client, "server_host", lambda: "http://server.test:8089"), \
                server_client.replying_for("wxid_ser"):
            await server_client.query_history("群A", limit=10)

        url, payload = post.await_args.args
        self.assertEqual(url, "http://server.test:8089/action/history")
        self.assertEqual(payload, {"chat_id": "群A", "limit": 10, "token": "token", "bot_id": "wxid_ser"})

    async def test_media_that_is_not_a_url_is_not_fetched(self):
        with self.assertLogs("ServerClient", level="ERROR"):
            self.assertIsNone(await server_client.fetch_media_bytes("wx_imgs/a.jpg"))


if __name__ == "__main__":
    unittest.main()
