"""When several bots share this AI, every reply goes back through the server of the bot that received the message."""

import asyncio
import types
import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks

from true_love_common.chat_msg import ChatMsg
from true_love_common.http.client import HttpResult

from true_love_ai.agent import server_client
from true_love_ai.api import trigger_routes


class AnsweringAgent:
    """Stands in for the agent loop: answers the sender with one text."""

    async def run(self, msg):
        await asyncio.sleep(0)
        await server_client.send_text(msg.sender_id, "hi")


class CrashingAgent:
    async def run(self, msg):
        raise RuntimeError("llm exploded")


def accepted(url):
    return HttpResult(
        method="POST", url=url, ok=True, status_code=200, headers={}, text="", content=b"",
        data={"code": 0}, cost_ms=1,
    )


class ReplyRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = types.SimpleNamespace(http=types.SimpleNamespace(token=["token"]))
        self.posted = []
        self.agent = AnsweringAgent()

        async def post(url, payload, timeout=None):
            self.posted.append((url, payload.get("receiver")))
            return accepted(url)

        for patcher in (
            patch.object(server_client, "get_config", return_value=config),
            patch.object(server_client, "async_post_json", post),
            patch.object(server_client, "server_host", lambda bot_id: f"http://{bot_id}-server:8088"),
            patch.object(trigger_routes, "verify_token", return_value=True),
            patch("true_love_ai.agent.skills.ensure_skills_loaded"),
            patch("true_love_ai.agent.agent_loop.get_agent_loop", side_effect=lambda: self.agent),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def trigger(self, sender, bot_id):
        tasks = BackgroundTasks()
        response = await trigger_routes.trigger(
            {"token": "token", "msg": ChatMsg(sender_id=sender, bot_id=bot_id).to_dict()}, tasks,
        )
        await tasks()
        return response

    async def test_reply_goes_back_through_the_server_of_the_bot(self):
        await self.trigger("alice", "win11-ser")

        self.assertEqual(self.posted, [("http://win11-ser-server:8088/action/send", "alice")])

    async def test_triggers_handled_at_the_same_time_keep_their_own_servers(self):
        await asyncio.gather(
            asyncio.create_task(self.trigger("alice", "win10-m8s")),
            asyncio.create_task(self.trigger("bob", "win11-ser")),
            asyncio.create_task(self.trigger("carol", "gcp-win")),
        )

        self.assertEqual(sorted(self.posted), [
            ("http://gcp-win-server:8088/action/send", "carol"),
            ("http://win10-m8s-server:8088/action/send", "alice"),
            ("http://win11-ser-server:8088/action/send", "bob"),
        ])

    async def test_failure_notice_also_goes_back_through_the_same_server(self):
        self.agent = CrashingAgent()

        with self.assertLogs("TriggerRoutes", level="ERROR"):
            await self.trigger("alice", "win11-ser")

        self.assertEqual(self.posted, [("http://win11-ser-server:8088/action/send", "alice")])

if __name__ == "__main__":
    unittest.main()
