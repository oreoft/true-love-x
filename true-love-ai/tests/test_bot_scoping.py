"""Calls to the server name the bot that is replying (conversations per bot are in test_agent_loop)."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_ai.agent import server_client


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
