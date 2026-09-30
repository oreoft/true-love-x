"""AI calls back with the bot of the message it handled; the server finds that bot's base and database."""

import unittest
from datetime import datetime, timedelta, timezone

from server_env import ServerCase
from true_love_server.core.db_engine import bot_session
from true_love_server.services import listen_store
from true_love_server.services.bot_registry import DEFAULT_BOT_ID
from true_love_server.services.group_message_repository import GroupMessageRepository
from true_love_common.chat_msg import ChatMsg


def later(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class SendTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.m8s = self.register("wxid_m8s")
        self.ser = self.register("wxid_ser")

    def test_reply_goes_out_through_the_base_of_the_bot(self):
        response = self.post("/action/send", bot_id="wxid_ser", receiver="群A", at_user="alice", content="hi")

        self.assertEqual(response["code"], 0)
        self.assertEqual(self.bases.sent(), [
            (f"{self.ser}/send/text", {"sendReceiver": "群A", "atReceiver": "alice", "content": "hi"})])

    def test_message_for_the_master_lets_the_base_choose_the_receiver(self):
        self.post("/action/send", bot_id="wxid_ser", is_master=True, content="started")

        self.assertEqual(self.bases.sent(), [(f"{self.ser}/send/text", {"is_master": True, "content": "started"})])

    def test_call_without_a_bot_uses_the_default_bot(self):
        default = self.register(DEFAULT_BOT_ID)

        self.post("/action/send", is_master=True, content="AI started")

        self.assertEqual(self.bases.sent(default), [(f"{default}/send/text", {"is_master": True, "content": "AI started"})])

    def test_unknown_bot_is_refused_without_sending(self):
        response = self.post("/action/send", bot_id="wxid_nobody", receiver="群A", content="hi")

        self.assertNotEqual(response["code"], 0)
        self.assertEqual(self.bases.sent(), [])

    def test_missing_receiver_or_content_is_refused(self):
        self.assertNotEqual(self.post("/action/send", bot_id="wxid_m8s", content="hi")["code"], 0)
        self.assertNotEqual(self.post("/action/send", bot_id="wxid_m8s", receiver="群A")["code"], 0)

    def test_base_failure_is_reported_to_ai(self):
        self.bases.reply(f"{self.m8s}/send/text", {"code": 105, "message": "WeChat offline"})

        response = self.post("/action/send", bot_id="wxid_m8s", receiver="群A", content="hi")

        self.assertIn("WeChat offline", response["message"])

    def test_generated_file_is_handed_to_base_as_a_url_on_ai(self):
        self.post("/action/send-file", bot_id="wxid_m8s", receiver="群A", path="gen_img/a.jpg")

        [(url, payload)] = self.bases.sent()
        self.assertEqual(url, f"{self.m8s}/send/file")
        self.assertEqual(payload["sendReceiver"], "群A")
        self.assertTrue(payload["url"].endswith("/media/gen_img/a.jpg"))

    def test_token_is_required(self):
        self.assertNotEqual(self.post("/action/send", token="wrong", bot_id="wxid_m8s", receiver="a", content="b")["code"], 0)


class HistoryTests(ServerCase):
    def test_history_comes_from_the_database_of_the_bot(self):
        self.register("wxid_m8s")
        self.register("wxid_ser")
        for bot_id in ("wxid_m8s", "wxid_ser"):
            with bot_session(bot_id) as db:
                GroupMessageRepository(db).save(ChatMsg(msg_id="1", chat_id="群A", sender_id="alice", content=bot_id))

        response = self.post("/action/history", bot_id="wxid_ser", chat_id="群A")

        self.assertEqual([m["content"] for m in response["data"]["messages"]], ["wxid_ser"])


class ReminderTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.register("wxid_m8s")
        self.register("wxid_ser")

    def add(self, bot_id, job_id="reminder_群A_1", receiver="群A"):
        return self.post("/action/reminder/add", bot_id=bot_id, job_id=job_id, target_time_iso=later(),
                         receiver=receiver, content="喝水")

    def test_reminders_of_one_bot_are_invisible_to_another(self):
        self.add("wxid_m8s")

        self.assertEqual(len(self.post("/action/reminder/query", bot_id="wxid_m8s", receiver="群A")["data"]["jobs"]), 1)
        self.assertEqual(self.post("/action/reminder/query", bot_id="wxid_ser", receiver="群A")["data"]["jobs"], [])

    def test_same_reminder_id_on_two_bots_does_not_clash(self):
        self.add("wxid_m8s")
        self.add("wxid_ser")

        self.post("/action/reminder/delete", bot_id="wxid_ser", job_id="reminder_群A_1")

        self.assertEqual(len(self.post("/action/reminder/query", bot_id="wxid_m8s")["data"]["jobs"]), 1)
        self.assertEqual(self.post("/action/reminder/query", bot_id="wxid_ser")["data"]["jobs"], [])

    def test_update_changes_the_content(self):
        self.add("wxid_m8s")

        self.post("/action/reminder/update", bot_id="wxid_m8s", job_id="reminder_群A_1", new_content="吃饭")

        [job] = self.post("/action/reminder/query", bot_id="wxid_m8s")["data"]["jobs"]
        self.assertEqual(job["content"], "吃饭")

    def test_reminder_in_the_past_is_refused(self):
        response = self.post("/action/reminder/add", bot_id="wxid_m8s", job_id="reminder_群A_1",
                             target_time_iso=later(-1), receiver="群A", content="喝水")

        self.assertNotEqual(response["code"], 0)

    def test_reminder_fires_through_its_own_bot(self):
        from true_love_server.services import reminder_service

        reminder_service._send_reminder("群A", "alice", "喝水", "reminder_群A_1", "wxid_ser")

        [(url, payload)] = self.bases.sent()
        self.assertEqual(url, "http://wxid_ser.base:5000/send/text")
        self.assertIn("喝水", payload["content"])


class ListenTests(ServerCase):
    def test_listen_is_added_on_the_base_of_the_bot_and_saved_in_its_list(self):
        callback = self.register("wxid_ser")

        response = self.post("/action/listen/add", bot_id="wxid_ser", chat_name="群A")

        self.assertEqual(response["code"], 0)
        self.assertEqual([url for url, _ in self.bases.sent()], [f"{callback}/execute/wx", f"{callback}/listen/add"])
        self.assertEqual(listen_store.list_all("wxid_ser"), ["群A"])

    def test_bot_on_another_platform_cannot_listen(self):
        self.register("lark_app", platform="lark")

        response = self.post("/action/listen/add", bot_id="lark_app", chat_name="群A")

        self.assertNotEqual(response["code"], 0)
        self.assertEqual(self.bases.sent(), [])


if __name__ == "__main__":
    unittest.main()
