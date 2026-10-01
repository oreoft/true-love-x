"""Every base reports its bot on each call; messages land in that bot's own database and go to AI as that bot."""

import unittest

from server_env import ServerCase, http_result
from true_love_server.core.db_engine import bot_session
from true_love_server.services import bot_registry, bot_settings, listen_store
from true_love_server.services.group_message_repository import GroupMessageRepository


def message(**fields):
    return {"platform": "wechat", "msg_type": "text", "msg_id": "m1", "sender_id": "alice", "chat_id": "群A",
            "is_group": True, "content": "hi", **fields}


class RegistrationTests(ServerCase):
    def test_first_call_registers_the_bot_and_creates_its_database(self):
        self.register("wxid_m8s", name="真爱粉")

        bot = bot_registry.get("wxid_m8s")
        self.assertEqual((bot.platform, bot.name, bot.callback), ("wechat", "真爱粉", "http://wxid_m8s.base:5000"))
        self.assertTrue((self.data_dir / "wxid_m8s.db").exists())

    def test_new_callback_replaces_the_old_one(self):
        self.register("wxid_m8s", callback="http://100.64.0.1:5000")
        self.register("wxid_m8s", callback="http://100.64.0.2:5000")

        self.assertEqual(bot_registry.get("wxid_m8s").callback, "http://100.64.0.2:5000")

    def test_bot_cannot_move_to_another_platform(self):
        self.register("wxid_m8s")

        response = self.post("/base/register", bot=self.bot("wxid_m8s", platform="lark"))

        self.assertNotEqual(response["code"], 0)
        self.assertEqual(bot_registry.get("wxid_m8s").platform, "wechat")

    def test_bot_id_that_is_not_a_safe_file_name_is_refused(self):
        response = self.post("/base/register", bot=self.bot("../platform"))

        self.assertNotEqual(response["code"], 0)
        self.assertEqual(list(self.data_dir.glob("*.db")), [self.data_dir / "platform.db"])

    def test_calls_without_the_token_or_the_bot_are_refused(self):
        self.assertNotEqual(self.post("/base/register", token="wrong", bot=self.bot("wxid_m8s"))["code"], 0)
        self.assertNotEqual(self.post("/base/register", bot={"bot_id": "wxid_m8s"})["code"], 0)
        self.assertEqual(bot_registry.list_all(), [])


class OnMessageTests(ServerCase):
    def messages(self, bot_id):
        with bot_session(bot_id) as db:
            return GroupMessageRepository(db).get_messages("群A")

    def test_message_is_stored_in_the_database_of_the_bot_that_received_it(self):
        self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message(content="from m8s"))
        self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=message(content="from ser"))

        self.assertEqual([m["content"] for m in self.messages("wxid_m8s")], ["from m8s"])
        self.assertEqual([m["content"] for m in self.messages("wxid_ser")], ["from ser"])

    def test_same_message_id_on_two_bots_is_not_a_duplicate(self):
        self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message(msg_id="same", is_at_me=True))
        self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=message(msg_id="same", is_at_me=True))

        self.assertEqual(len(self.ai_calls), 2)

    def test_repeated_message_triggers_ai_once(self):
        for _ in range(2):
            self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message(is_at_me=True))

        self.assertEqual(len(self.ai_calls), 1)

    def test_ai_gets_the_bot_that_received_the_message_whatever_the_body_says(self):
        self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=message(is_group=False, bot_id="spoofed"))

        [(url, payload)] = self.ai_calls
        self.assertTrue(url.endswith("/trigger"))
        self.assertEqual(payload["msg"]["bot_id"], "wxid_ser")

    def test_plain_group_message_is_only_stored(self):
        self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message())

        self.assertEqual(self.ai_calls, [])
        self.assertEqual(len(self.messages("wxid_m8s")), 1)

    def test_message_resent_after_the_user_was_told_is_only_stored(self):
        self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message(is_at_me=True), archive_only=True)

        self.assertEqual(self.ai_calls, [])
        self.assertEqual(len(self.messages("wxid_m8s")), 1)

    def switch_on(self, bot_id, *kinds):
        self.register(bot_id)
        for kind in kinds:
            bot_settings.set_bool(bot_id, f"auto_ai_{kind}", True)

    def test_group_link_pdf_image_and_note_go_to_ai_only_when_switched_on(self):
        msgs = [message(msg_id="link", msg_type="link", link_msg={"url": "https://mp.weixin.qq.com/s/x"}),
                message(msg_id="pdf", msg_type="file",
                        file_msg={"file_name": "a.pdf", "resource": {"ref": "wx_imgs/a.pdf", "source": "local"}}),
                message(msg_id="image", msg_type="image", image_msg={"resource": {"ref": "wx_imgs/a.jpg"}}),
                message(msg_id="note", msg_type="note", content="笔记玩AI最后就是玩数据中心")]
        for msg in msgs:
            self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=msg)
        self.assertEqual(self.ai_calls, [])

        self.switch_on("wxid_m8s", *bot_settings.AUTO_AI_TYPES)
        for msg in msgs:
            self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=dict(msg, msg_id=msg["msg_id"] + "2"))

        self.assertEqual([payload["msg"]["msg_type"] for _, payload in self.ai_calls], ["link", "file", "image", "note"])

    def test_auto_ai_skips_messages_with_nothing_to_read(self):
        self.switch_on("wxid_m8s", *bot_settings.AUTO_AI_TYPES)

        for msg in (message(msg_id="1", msg_type="link", link_msg={"url": None}),
                    message(msg_id="2", msg_type="file", file_msg={"file_name": "a.pdf"}),
                    message(msg_id="3", msg_type="file",
                            file_msg={"file_name": "a.docx", "resource": {"ref": "wx_imgs/a.docx"}}),
                    message(msg_id="4", msg_type="image"),
                    message(msg_id="5", msg_type="note", content="笔记"),
                    message(msg_id="6", msg_type="emotion", content="[动画表情]")):
            self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=msg)

        self.assertEqual(self.ai_calls, [])

    def test_each_switch_only_covers_its_own_type(self):
        self.switch_on("wxid_m8s", "file")

        self.post("/base/on-message", bot=self.bot("wxid_m8s"),
                  msg=message(msg_type="link", link_msg={"url": "https://mp.weixin.qq.com/s/x"}))

        self.assertEqual(self.ai_calls, [])

    def test_each_type_has_its_own_limit_in_each_group(self):
        self.switch_on("wxid_m8s", "image", "link")
        bot_settings.set_limit("wxid_m8s", "auto_ai_image_limit", {"count": 2, "seconds": 600})

        for i in range(3):
            self.post("/base/on-message", bot=self.bot("wxid_m8s"),
                      msg=message(msg_id=f"img{i}", sender_id=f"user{i}", msg_type="image",
                                  image_msg={"resource": {"ref": f"wx_imgs/{i}.jpg"}}))
        self.post("/base/on-message", bot=self.bot("wxid_m8s"),
                  msg=message(msg_id="img-b", chat_id="群B", msg_type="image", image_msg={"resource": {"ref": "wx_imgs/b.jpg"}}))
        self.post("/base/on-message", bot=self.bot("wxid_m8s"),
                  msg=message(msg_id="link", msg_type="link", link_msg={"url": "https://mp.weixin.qq.com/s/x"}))

        sent = [(payload["msg"]["chat_id"], payload["msg"]["msg_type"]) for _, payload in self.ai_calls]
        self.assertEqual(sent, [("群A", "image"), ("群A", "image"), ("群B", "image"), ("群A", "link")])
        self.assertEqual(len(self.messages("wxid_m8s")), 4)

    def test_personal_rate_limit_follows_the_bot_setting(self):
        self.register("wxid_m8s")
        bot_settings.set_limit("wxid_m8s", bot_settings.AI_RATE_LIMIT, {"count": 2, "seconds": 600})

        for i in range(4):
            self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message(msg_id=f"m{i}", is_at_me=True))

        self.assertEqual(len(self.ai_calls), 2)
        [(_, payload)] = self.bases.sent()
        self.assertIn("太快", payload["content"])

    def test_media_is_handed_to_ai_as_a_url_on_the_base_that_received_it(self):
        self.post("/base/on-message", bot=self.bot("wxid_ser", callback="http://100.64.0.9:5000"),
                  msg=message(is_group=False, msg_type="image",
                              image_msg={"resource": {"ref": "wx_imgs/a.jpg", "source": "local"}}))

        resource = self.ai_calls[0][1]["msg"]["image_msg"]["resource"]
        self.assertEqual(resource, {"ref": "http://100.64.0.9:5000/media/wx_imgs/a.jpg", "source": "http"})

    def test_user_is_told_through_the_same_bot_when_ai_is_unreachable(self):
        self.ai_answer = lambda msg: ConnectionError("refused")

        with self.assertLogs("MessageService", level="ERROR"):
            self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=message(is_at_me=True))

        [(url, payload)] = self.bases.sent()
        self.assertEqual(url, "http://wxid_ser.base:5000/send/text")
        self.assertEqual((payload["sendReceiver"], payload["atReceiver"]), ("群A", "alice"))

    def test_user_is_told_when_ai_rejects_the_message(self):
        self.ai_answer = lambda msg: http_result("http://ai.test/trigger", {"code": 500, "message": "busy"})

        with self.assertLogs("MessageService", level="ERROR"):
            self.post("/base/on-message", bot=self.bot("wxid_m8s"), msg=message(is_group=False))

        [(url, payload)] = self.bases.sent()
        self.assertEqual((url, payload["sendReceiver"]), ("http://wxid_m8s.base:5000/send/text", "alice"))


class ListenListTests(ServerCase):
    def test_each_wechat_bot_gets_its_own_list(self):
        self.register("wxid_m8s")
        self.register("wxid_ser")
        listen_store.add("wxid_m8s", "群A")
        listen_store.add("wxid_ser", "群B")

        response = self.post("/base/listen/list", bot=self.bot("wxid_ser"))

        self.assertEqual(response["data"], {"chats": ["群B"], "private_poll": False, "auto_accept_friends": False, "group_reply": ["at"]})

    def test_private_poll_setting_comes_with_the_list(self):
        self.register("wxid_ser")
        bot_settings.set_bool("wxid_ser", bot_settings.PRIVATE_POLL, True)

        response = self.post("/base/listen/list", bot=self.bot("wxid_ser"))

        self.assertTrue(response["data"]["private_poll"])

    def test_bot_on_another_platform_has_no_listen_list(self):
        response = self.post("/base/listen/list", bot=self.bot("lark_app", platform="lark"))

        self.assertNotEqual(response["code"], 0)


if __name__ == "__main__":
    unittest.main()
