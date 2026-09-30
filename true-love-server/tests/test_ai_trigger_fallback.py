"""When AI does not accept a message, the server must tell the user instead of only logging."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from true_love_common.chat_msg import ChatMsg, ImageMsg, ResourceRef
from true_love_common.http.client import HttpResult


SOURCE = Path(__file__).parents[1] / "src/true_love_server"


def module(name, path=None, **attributes):
    result = types.ModuleType(name)
    if path is not None:
        result.__path__ = [str(path)]
    result.__dict__.update(attributes)
    return result


def ai_response(status_code=200, data=None):
    return HttpResult(
        method="POST", url="http://ai.test/trigger", ok=200 <= status_code < 400,
        status_code=status_code, headers={}, text="", content=b"", data=data, cost_ms=1,
    )


class RoutesCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = types.SimpleNamespace(HTTP_TOKEN=["token"])
        self.repository = Mock()
        self.repository.save.return_value = True
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        # Import the real routes module without loading config, DB or listener state.
        dependencies = {
            "true_love_server": module("true_love_server", SOURCE, Config=lambda: config),
            "true_love_server.api": module("true_love_server.api", SOURCE / "api"),
            "true_love_server.core": module("true_love_server.core", SOURCE / "core", Config=lambda: config),
            "true_love_server.services": module("true_love_server.services", SOURCE / "services"),
            "true_love_server.api.deps": module("true_love_server.api.deps", verify_token=Mock()),
            "true_love_server.core.db_engine": module("true_love_server.core.db_engine", SessionLocal=lambda: session),
            "true_love_server.services.group_message_repository": module(
                "true_love_server.services.group_message_repository", GroupMessageRepository=lambda _: self.repository),
            "true_love_server.services.listen_manager": module(
                "true_love_server.services.listen_manager", get_listen_manager=Mock()),
            "true_love_server.services.loki_client": module(
                "true_love_server.services.loki_client", get_loki_client=Mock()),
            "true_love_server.services.reminder_service": module("true_love_server.services.reminder_service"),
            "true_love_server.services.task_service": module("true_love_server.services.task_service"),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        self.routes = importlib.import_module("true_love_server.api.routes")

        self.send_text = AsyncMock(return_value=(True, ""))
        self.send_to_master = AsyncMock(return_value=(True, ""))
        self.post_json = Mock(return_value=ai_response(data={"code": 0}))
        for patcher in (
            patch.object(self.routes.base_client, "send_text", self.send_text),
            patch.object(self.routes.base_client, "send_to_master", self.send_to_master, create=True),
            patch.object(self.routes, "post_json", self.post_json),
            patch.object(self.routes, "ai_host", lambda: "http://ai.test"),
            patch.object(self.routes, "machine_bot_id", lambda: "win10-m8s"),
            patch.object(self.routes, "bot_hosts", lambda bot_id: types.SimpleNamespace(base=f"http://{bot_id}-base:5000")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)



class AiTriggerFallbackTests(RoutesCase):
    async def test_private_message_gets_notice_when_ai_is_unreachable(self):
        self.post_json.side_effect = ConnectionError("refused")

        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice"))

        self.send_text.assert_awaited_once_with(
            "alice", "", self.routes.AI_UNAVAILABLE_REPLY, platform="wechat")

    async def test_group_mention_gets_notice_when_ai_returns_http_error(self):
        self.post_json.return_value = ai_response(status_code=502)
        msg = ChatMsg(sender_id="alice", chat_id="room", is_group=True, is_at_me=True)

        await self.routes._handle_incoming_message(msg)

        self.send_text.assert_awaited_once_with(
            "room", "alice", self.routes.AI_UNAVAILABLE_REPLY, platform="wechat")

    async def test_notice_when_ai_rejects_the_trigger(self):
        self.post_json.return_value = ai_response(data={"code": 401, "message": "token error"})

        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice"))

        self.send_text.assert_awaited_once()

    async def test_accepted_trigger_sends_nothing(self):
        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice"))

        self.post_json.assert_called_once()
        self.send_text.assert_not_awaited()

    async def test_trigger_goes_to_the_ai_address(self):
        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice"))

        self.assertEqual(self.post_json.call_args.args[0], "http://ai.test/trigger")

    async def test_ai_learns_which_bot_the_message_came_from(self):
        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice", bot_id="win11-ser"))

        self.assertEqual(self.post_json.call_args.args[1]["msg"]["bot_id"], "win11-ser")

    async def test_lark_message_without_a_bot_is_answered_through_this_server(self):
        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice", platform="lark"))

        self.assertEqual(self.post_json.call_args.args[1]["msg"]["bot_id"], "win10-m8s")

    async def test_wechat_media_is_handed_to_ai_as_a_url_on_the_base_that_received_it(self):
        image = ImageMsg(resource=ResourceRef(ref="wx_imgs/a.jpg"))
        quoted = ChatMsg(msg_type="image", image_msg=ImageMsg(resource=ResourceRef(ref="wx_imgs/b.jpg")))
        msg = ChatMsg(sender_id="alice", msg_type="image", image_msg=image, refer_msg=quoted, bot_id="win11-ser")

        await self.routes._handle_incoming_message(msg)

        sent = self.post_json.call_args.args[1]["msg"]
        self.assertEqual(sent["image_msg"]["resource"],
                         {"ref": "http://win11-ser-base:5000/media/wx_imgs/a.jpg", "source": "http"})
        self.assertEqual(sent["refer_msg"]["image_msg"]["resource"]["ref"],
                         "http://win11-ser-base:5000/media/wx_imgs/b.jpg")

    async def test_lark_media_is_passed_through_untouched(self):
        image = ImageMsg(resource=ResourceRef(ref="img_v3_key"))
        await self.routes._handle_incoming_message(
            ChatMsg(sender_id="alice", platform="lark", msg_type="image", image_msg=image))

        self.assertEqual(self.post_json.call_args.args[1]["msg"]["image_msg"]["resource"]["ref"], "img_v3_key")

    async def test_plain_group_message_never_triggers_ai(self):
        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice", chat_id="room", is_group=True))

        self.post_json.assert_not_called()
        self.send_text.assert_not_awaited()

    async def test_duplicate_message_is_not_triggered_again(self):
        self.repository.save.return_value = False

        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice"))

        self.post_json.assert_not_called()
        self.send_text.assert_not_awaited()

    async def test_storage_failure_still_triggers_ai(self):
        self.repository.save.side_effect = RuntimeError("db down")

        await self.routes._handle_incoming_message(ChatMsg(sender_id="alice"))

        self.post_json.assert_called_once()
        self.send_text.assert_not_awaited()


class PushToMasterTests(RoutesCase):
    """Outside callers push notices to the master without knowing who that is; base knows."""

    async def test_push_addressed_to_master_is_handed_to_base_as_a_message_for_its_master(self):
        response = await self.routes.send_msg({"token": "token", "sendReceiver": "master", "content": "deployed"})

        self.send_to_master.assert_awaited_once_with("deployed")
        self.send_text.assert_not_awaited()
        self.assertEqual(response.code, 0)

    async def test_push_to_anyone_else_is_refused(self):
        with self.assertRaises(self.routes.ValidationException):
            await self.routes.send_msg({"token": "token", "sendReceiver": "委员会", "content": "hi"})

        self.send_to_master.assert_not_awaited()
        self.send_text.assert_not_awaited()

    async def test_push_without_content_is_refused(self):
        with self.assertRaises(self.routes.ValidationException):
            await self.routes.send_msg({"token": "token", "sendReceiver": "master", "content": ""})

        self.send_to_master.assert_not_awaited()

    async def test_caller_learns_when_base_could_not_deliver(self):
        self.send_to_master.return_value = (False, "WeChat offline")

        with self.assertRaisesRegex(self.routes.ValidationException, "WeChat offline"):
            await self.routes.send_msg({"token": "token", "sendReceiver": "master", "content": "deployed"})


if __name__ == "__main__":
    unittest.main()
