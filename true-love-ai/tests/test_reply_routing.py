"""Every bot shares one server; each reply names the bot that received the message, so it goes out from that account."""

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
        self.payloads = []
        self.agent = AnsweringAgent()

        async def post(url, payload, timeout=None):
            self.posted.append((url, payload.get("bot_id"), payload.get("receiver")))
            self.payloads.append(payload)
            return accepted(url)

        for patcher in (
            patch.object(server_client, "get_config", return_value=config),
            patch.object(server_client, "async_post_json", post),
            patch.object(server_client, "server_host", lambda: "http://server.test:8089"),
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

    async def test_reply_names_the_bot_that_received_the_message(self):
        await self.trigger("alice", "wxid_ser")

        self.assertEqual(self.posted, [("http://server.test:8089/action/send", "wxid_ser", "alice")])

    async def test_triggers_handled_at_the_same_time_keep_their_own_bots(self):
        await asyncio.gather(
            asyncio.create_task(self.trigger("alice", "wxid_m8s")),
            asyncio.create_task(self.trigger("bob", "wxid_ser")),
            asyncio.create_task(self.trigger("carol", "lark_app")),
        )

        self.assertEqual(sorted(self.posted), [
            ("http://server.test:8089/action/send", "lark_app", "carol"),
            ("http://server.test:8089/action/send", "wxid_m8s", "alice"),
            ("http://server.test:8089/action/send", "wxid_ser", "bob"),
        ])

    async def test_failure_notice_also_names_the_same_bot(self):
        self.agent = CrashingAgent()

        with self.assertLogs("TriggerRoutes", level="ERROR"):
            await self.trigger("alice", "wxid_ser")

        self.assertEqual(self.posted, [("http://server.test:8089/action/send", "wxid_ser", "alice")])

    async def test_reply_names_the_message_it_quotes_only_when_there_is_one(self):
        await server_client.send_text("room", "hi", "alice", "m1")
        await server_client.send_text("alice", "hi")

        self.assertEqual(self.payloads[0]["reply_msg_id"], "m1")
        self.assertNotIn("reply_msg_id", self.payloads[1])


if __name__ == "__main__":
    unittest.main()
