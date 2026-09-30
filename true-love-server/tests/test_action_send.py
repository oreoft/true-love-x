"""AI sends its replies and its own notices through the server."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


SOURCE = Path(__file__).parents[1] / "src/true_love_server"


def module(name, path=None, **attributes):
    result = types.ModuleType(name)
    if path is not None:
        result.__path__ = [str(path)]
    result.__dict__.update(attributes)
    return result


class ActionSendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.send_text = AsyncMock(return_value=(True, ""))
        self.send_to_master = AsyncMock(return_value=(True, ""))
        self.send_file = AsyncMock(return_value=(True, ""))
        base_client = types.SimpleNamespace(
            send_text=self.send_text, send_to_master=self.send_to_master, send_file=self.send_file)
        dependencies = {
            "true_love_server": module("true_love_server", SOURCE, Config=Mock()),
            "true_love_server.api": module("true_love_server.api", SOURCE / "api"),
            "true_love_server.api.deps": module("true_love_server.api.deps", verify_token=Mock()),
            "true_love_server.services": module(
                "true_love_server.services", SOURCE / "services",
                base_client=base_client, reminder_service=Mock()),
            "true_love_server.services.listen_manager": module(
                "true_love_server.services.listen_manager", get_listen_manager=Mock()),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        self.routes = importlib.import_module("true_love_server.api.action_routes")
        ai = patch.object(self.routes, "ai_host", lambda: "http://ai.test:8088")
        ai.start()
        self.addCleanup(ai.stop)

    async def test_generated_file_is_handed_to_base_as_a_url_on_ai(self):
        await self.routes.action_send_file(
            {"token": "token", "receiver": "委员会", "path": "gen_img/abc.jpg", "platform": "wechat"})

        self.send_file.assert_awaited_once_with("http://ai.test:8088/media/gen_img/abc.jpg", "委员会", platform="wechat")

    async def test_reply_goes_to_the_chat_it_answers(self):
        await self.routes.action_send(
            {"token": "token", "receiver": "委员会", "at_user": "alice", "content": "hi", "platform": "wechat"})

        self.send_text.assert_awaited_once_with("委员会", "alice", "hi", platform="wechat")
        self.send_to_master.assert_not_awaited()

    async def test_notice_for_the_master_needs_no_receiver(self):
        response = await self.routes.action_send({"token": "token", "is_master": True, "content": "AI started"})

        self.send_to_master.assert_awaited_once_with("AI started")
        self.send_text.assert_not_awaited()
        self.assertEqual(response.code, 0)

    async def test_notice_for_the_master_without_content_is_refused(self):
        with self.assertRaises(self.routes.ValidationException):
            await self.routes.action_send({"token": "token", "is_master": True, "content": ""})

        self.send_to_master.assert_not_awaited()

    async def test_message_without_a_receiver_is_refused(self):
        with self.assertRaises(self.routes.ValidationException):
            await self.routes.action_send({"token": "token", "content": "hi"})

        self.send_text.assert_not_awaited()

    async def test_ai_learns_when_the_master_could_not_be_reached(self):
        self.send_to_master.return_value = (False, "No master is configured for this machine")

        with self.assertRaisesRegex(self.routes.ValidationException, "No master is configured"):
            await self.routes.action_send({"token": "token", "is_master": True, "content": "AI started"})


if __name__ == "__main__":
    unittest.main()
