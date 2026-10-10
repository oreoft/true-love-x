"""tl-admin's chat and friends pages drive the bot's WeChat through its base; the server only relays."""

import unittest
from unittest.mock import AsyncMock, patch

from server_env import ServerCase
from true_love_common.http.client import HttpResult


class ChatTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.callback = self.register("wxid_ser")

    def test_session_list_comes_from_the_base(self):
        sessions = {"sessions": [{"name": "委员会", "new_count": 2, "ismute": True}]}
        self.bases.reply(f"{self.callback}/chat/sessions", {"code": 0, "data": sessions})

        self.assertEqual(self.get("/admin/bots/wxid_ser/wechat/sessions")["data"], sessions)

    def test_messages_are_read_live_and_history_is_capped(self):
        read = {"chat_name": "委员会", "messages": [{"id": "m1", "content": "hi"}]}
        self.bases.reply(f"{self.callback}/chat/messages", {"code": 0, "data": read})

        data = self.get("/admin/bots/wxid_ser/wechat/messages", chat_name="委员会", history=5000)["data"]

        self.assertEqual(data, read)
        self.assertEqual(self.bases.sent()[-1][1], {"chat_name": "委员会", "history": 100})

    def test_text_is_sent_with_the_people_to_mention(self):
        response = self.post("/admin/bots/wxid_ser/wechat/send-text", token=None,
                             chat_name="委员会", content="开会了", at=["纯路人", ""])

        self.assertEqual(response["code"], 0, response)
        self.assertEqual(self.bases.sent()[-1], (f"{self.callback}/send/text",
                                                 {"sendReceiver": "委员会", "atReceiver": ["纯路人"], "content": "开会了"}))

    def test_failed_send_reports_why(self):
        self.bases.reply(f"{self.callback}/send/text", {"code": 1003, "message": "Send failed, please retry"})

        response = self.post("/admin/bots/wxid_ser/wechat/send-text", token=None, chat_name="委员会", content="hi")

        self.assertNotEqual(response["code"], 0)
        self.assertIn("Send failed", response["message"])

    def test_uploaded_file_goes_through_r2_to_the_base(self):
        with patch("true_love_server.api.admin_wechat_routes.r2.upload",
                   AsyncMock(return_value="https://r2.test/a.pdf")) as upload:
            response = self.client.post("/admin/bots/wxid_ser/wechat/send-file",
                                        params={"chat_name": "委员会", "filename": "../报告.pdf"},
                                        content=b"%PDF").json()

        self.assertEqual(response["code"], 0, response)
        self.assertEqual(upload.call_args.args[1].name, "报告.pdf")
        self.assertEqual(self.bases.sent()[-1], (f"{self.callback}/send/file",
                                                 {"url": "https://r2.test/a.pdf", "sendReceiver": "委员会"}))

    def test_empty_upload_is_refused(self):
        response = self.client.post("/admin/bots/wxid_ser/wechat/send-file",
                                    params={"chat_name": "委员会", "filename": "a.txt"}, content=b"").json()

        self.assertNotEqual(response["code"], 0)
        self.assertEqual(self.bases.sent(), [])

    def test_quote_and_tickle_name_the_message(self):
        self.post("/admin/bots/wxid_ser/wechat/quote", token=None, chat_name="委员会", msg_id="m1", content="收到")
        self.post("/admin/bots/wxid_ser/wechat/tickle", token=None, chat_name="委员会", msg_id="m1")

        self.assertEqual(self.bases.sent()[-2:], [
            (f"{self.callback}/chat/quote", {"chat_name": "委员会", "msg_id": "m1", "content": "收到"}),
            (f"{self.callback}/chat/tickle", {"chat_name": "委员会", "msg_id": "m1"}),
        ])

    def test_media_is_downloaded_by_the_base_and_handed_over(self):
        self.bases.reply(f"{self.callback}/chat/media", {"code": 0, "data": {"path": "wx_imgs/a.jpg"}})
        fetched = []

        async def fetch(url, **kwargs):
            fetched.append(url)
            return HttpResult(method="GET", url=url, ok=True, status_code=200, content=b"JPEG")

        with patch("true_love_server.services.base_client._client.async_get", fetch):
            response = self.client.get("/admin/bots/wxid_ser/wechat/media",
                                       params={"chat_name": "委员会", "msg_id": "m1", "quoted": "true"})

        self.assertEqual((response.status_code, response.content), (200, b"JPEG"))
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(self.bases.sent()[-1][1], {"chat_name": "委员会", "msg_id": "m1", "quoted": True})
        self.assertEqual(fetched, [f"{self.callback}/media/wx_imgs/a.jpg"])

    def test_media_the_base_cannot_download_is_an_error(self):
        self.bases.reply(f"{self.callback}/chat/media", {"code": 107, "message": "下载失败"})

        response = self.client.get("/admin/bots/wxid_ser/wechat/media", params={"chat_name": "委员会", "msg_id": "m1"})

        self.assertNotEqual(response.json()["code"], 0)

    def test_base_failure_is_an_error(self):
        self.bases.reply(f"{self.callback}/chat/tickle", {"code": 107, "message": "message is gone"})

        response = self.post("/admin/bots/wxid_ser/wechat/tickle", token=None, chat_name="委员会", msg_id="m1")

        self.assertNotEqual(response["code"], 0)
        self.assertIn("message is gone", response["message"])

    def test_other_platforms_have_no_chat_page(self):
        self.register("lark_app", platform="lark")

        self.assertNotEqual(self.get("/admin/bots/lark_app/wechat/sessions")["code"], 0)
        self.assertEqual(self.bases.sent(), [])


class FriendTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.callback = self.register("wxid_ser")

    def test_requests_are_listed(self):
        requests = {"requests": [{"content": "小明 我是小明", "acceptable": True}]}
        self.bases.reply(f"{self.callback}/friends/requests", {"code": 0, "data": requests})

        self.assertEqual(self.get("/admin/bots/wxid_ser/wechat/friend-requests")["data"], requests)

    def test_accept_passes_the_remark(self):
        self.post("/admin/bots/wxid_ser/wechat/friend-accept", token=None, content="小明 我是小明", remark="小明")

        self.assertEqual(self.bases.sent()[-1][1], {"content": "小明 我是小明", "remark": "小明"})

    def test_add_needs_a_keyword(self):
        self.assertNotEqual(self.post("/admin/bots/wxid_ser/wechat/friend-add", token=None, keywords=" ")["code"], 0)
        self.post("/admin/bots/wxid_ser/wechat/friend-add", token=None, keywords="wxid_x", addmsg="你好")

        self.assertEqual(self.bases.sent()[-1][1], {"keywords": "wxid_x", "addmsg": "你好", "remark": ""})

    def test_edit_needs_a_new_remark(self):
        self.assertNotEqual(self.post("/admin/bots/wxid_ser/wechat/friend-edit", token=None, chat_name="小明")["code"], 0)
        self.post("/admin/bots/wxid_ser/wechat/friend-edit", token=None, chat_name="小明", remark="小明同学")

        self.assertEqual(self.bases.sent()[-1][1], {"chat_name": "小明", "remark": "小明同学"})


if __name__ == "__main__":
    unittest.main()
