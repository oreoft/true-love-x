"""Each WeChat bot's listen list lives in its own database; base fetches it instead of reading a shared file."""

import unittest

from server_env import ServerCase
from true_love_server.services import listen_store
from true_love_server.services.listen_manager import get_listen_manager


class ListenStoreTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.register("wxid_m8s")
        self.register("wxid_ser")

    def test_chats_are_listed_in_the_order_they_were_added(self):
        for chat in ("群A", "好友B", "群C"):
            self.assertTrue(listen_store.add("wxid_m8s", chat))

        self.assertEqual(listen_store.list_all("wxid_m8s"), ["群A", "好友B", "群C"])
        self.assertEqual(listen_store.list_all("wxid_ser"), [])

    def test_adding_twice_keeps_one_entry(self):
        self.assertTrue(listen_store.add("wxid_m8s", "群A"))
        self.assertFalse(listen_store.add("wxid_m8s", "群A"))

        self.assertEqual(listen_store.list_all("wxid_m8s"), ["群A"])

    def test_removing_an_absent_chat_is_harmless(self):
        listen_store.add("wxid_m8s", "群A")

        self.assertFalse(listen_store.remove("wxid_m8s", "群B"))
        self.assertTrue(listen_store.remove("wxid_m8s", "群A"))
        self.assertFalse(listen_store.exists("wxid_m8s", "群A"))


class ListenManagerTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.callback = self.register("wxid_m8s")
        listen_store.add("wxid_m8s", "deleted chat")
        listen_store.add("wxid_m8s", "kept chat")
        self.manager = get_listen_manager("wxid_m8s")

    def base_answers(self, success):
        answer = {"code": 0 if success else 107, "message": "ok" if success else "listener was not running"}
        for path in ("/execute/wx", "/listen/add"):
            self.bases.reply(f"{self.callback}{path}", answer)

    def test_chat_is_saved_only_after_base_starts_listening(self):
        self.base_answers(False)
        with self.assertLogs("ListenManager", level="ERROR"):
            self.assertFalse(self.run_async(self.manager.add_listen("new chat"))["success"])
        self.assertFalse(listen_store.exists("wxid_m8s", "new chat"))

        self.base_answers(True)
        self.assertTrue(self.run_async(self.manager.add_listen("new chat"))["success"])
        self.assertEqual(listen_store.list_all("wxid_m8s"), ["deleted chat", "kept chat", "new chat"])

    def test_removal_is_saved_whether_or_not_base_was_listening(self):
        for success in (True, False):
            with self.subTest(base_success=success):
                listen_store.add("wxid_m8s", "deleted chat")
                self.base_answers(success)

                result = self.run_async(self.manager.remove_listen("deleted chat"))

                self.assertTrue(result["success"])
                self.assertEqual(listen_store.list_all("wxid_m8s"), ["kept chat"])
        self.assertEqual(self.bases.sent()[-1],
                         (f"{self.callback}/execute/wx", {"name": "RemoveListenChat", "params": {"nickname": "deleted chat"}}))

    def test_reset_keeps_the_saved_chat(self):
        self.base_answers(False)

        result = self.run_async(self.manager.remove_listen("deleted chat", skip_store=True))

        self.assertTrue(result["success"])
        self.assertEqual(listen_store.list_all("wxid_m8s"), ["deleted chat", "kept chat"])

    def test_adding_a_listen_waits_for_base_to_finish_its_retries(self):
        seen = {}
        post = self.bases.post

        async def record(url, headers=None, data=None, timeout=None):
            seen[url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1]] = timeout
            return await post(url, headers=headers, data=data, timeout=timeout)

        from unittest.mock import patch
        from server_env import base_http
        with patch.object(base_http, "async_post", record):
            self.run_async(self.manager.add_listen("new chat"))

        self.assertEqual(seen["listen/add"][1], 30)
        self.assertEqual(seen["execute/wx"][1], 10)

    @staticmethod
    def run_async(coro):
        import asyncio
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
