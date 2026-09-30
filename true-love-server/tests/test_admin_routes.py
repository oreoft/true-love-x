"""tl-admin lists every bot and manages one bot at a time; channel-only pages follow the bot's capabilities."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from server_env import ServerCase
from true_love_common.chat_msg import ChatMsg
from true_love_server.core.db_engine import bot_session
from true_love_server.services import listen_store, task_service
from true_love_server.services.bot_registry import DEFAULT_BOT_ID
from true_love_server.services.group_message_repository import GroupMessageRepository


def later(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class OverviewTests(ServerCase):
    def test_every_bot_is_listed_with_what_it_can_do_and_whether_its_base_is_up(self):
        m8s = self.register(DEFAULT_BOT_ID, name="真爱粉")
        self.register("lark_app", platform="lark")
        self.bases.statuses[m8s] = {"wx_online": True, "self_name": "真爱粉", "since": "2026-09-30T08:00:00"}
        listen_store.add(DEFAULT_BOT_ID, "群A")

        data = self.get("/admin/bots")["data"]

        first, second = data["bots"]
        self.assertEqual((first["bot_id"], first["capabilities"], first["is_default"]), (DEFAULT_BOT_ID, ["listen"], True))
        self.assertEqual((first["status"]["online"], first["listen_count"]), (True, 1))
        self.assertEqual((second["platform"], second["capabilities"], second["listen_count"]), ("lark", [], None))
        self.assertEqual((second["status"]["reachable"], second["status"]["online"]), (False, False))

    def test_next_run_is_the_earliest_reminder_or_task(self):
        self.register("wxid_m8s")
        soon, later_on = later(1), later(5)
        self.post("/admin/bots/wxid_m8s/reminders/add", receiver="群A", content="喝水", target_time_iso=later_on)
        self.post("/admin/bots/wxid_m8s/reminders/add", receiver="群B", content="吃饭", target_time_iso=soon)

        card = self.get("/admin/bots/wxid_m8s")["data"]

        self.assertEqual(datetime.fromisoformat(card["next_run_time"]), datetime.fromisoformat(soon))

    def test_unknown_bot_is_refused(self):
        self.assertNotEqual(self.get("/admin/bots/wxid_nobody")["code"], 0)


class MessageTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.register("wxid_m8s")
        with bot_session("wxid_m8s") as db:
            repo = GroupMessageRepository(db)
            for i in range(1, 6):
                repo.save(ChatMsg(msg_id=f"a{i}", chat_id="群A", chat_name="群A", sender_id="alice",
                                  content=f"hello {i}" if i % 2 else f"bye {i}", is_group=True))
            repo.save(ChatMsg(msg_id="b1", chat_id="群B", sender_id="bob", content="yo", is_group=True))

    def test_chats_are_listed_latest_first(self):
        chats = self.get("/admin/bots/wxid_m8s/chats")["data"]["chats"]

        self.assertEqual([(c["chat_id"], c["count"]) for c in chats], [("群B", 1), ("群A", 5)])

    def test_messages_are_paged_backwards_by_id(self):
        first = self.get("/admin/bots/wxid_m8s/messages", chat_id="群A", limit=2)["data"]
        second = self.get("/admin/bots/wxid_m8s/messages", chat_id="群A", limit=2,
                          tail_id=first["next_tail_id"])["data"]
        last = self.get("/admin/bots/wxid_m8s/messages", chat_id="群A", limit=2,
                        tail_id=second["next_tail_id"])["data"]

        self.assertEqual([m["content"] for m in first["messages"]], ["bye 4", "hello 5"])
        self.assertEqual([m["content"] for m in second["messages"]], ["bye 2", "hello 3"])
        self.assertEqual(([m["content"] for m in last["messages"]], last["next_tail_id"]), (["hello 1"], None))

    def test_keyword_filters_the_page(self):
        data = self.get("/admin/bots/wxid_m8s/messages", chat_id="群A", keyword="bye")["data"]

        self.assertEqual([m["content"] for m in data["messages"]], ["bye 2", "bye 4"])

    def test_receiver_choices_are_listened_chats_and_chats_seen(self):
        listen_store.add("wxid_m8s", "群C")

        self.assertEqual(self.get("/admin/bots/wxid_m8s/receivers")["data"]["receivers"], ["群C", "群B", "群A"])


class ReminderAndTaskTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.register("wxid_m8s")
        self.register("wxid_ser")

    def test_reminder_is_added_edited_and_deleted_for_one_bot(self):
        self.post("/admin/bots/wxid_ser/reminders/add", receiver="群A", content="喝水", target_time_iso=later())
        [job] = self.get("/admin/bots/wxid_ser/reminders")["data"]["jobs"]

        edited = self.post("/admin/bots/wxid_ser/reminders/update", job_id=job["job_id"], receiver="群B",
                           content="吃饭", target_time_iso=later(2))["data"]
        [after] = self.get("/admin/bots/wxid_ser/reminders")["data"]["jobs"]
        self.assertEqual((after["job_id"], after["receiver"], after["content"]), (edited["job_id"], "群B", "吃饭"))
        self.assertEqual(self.get("/admin/bots/wxid_m8s/reminders")["data"]["jobs"], [])

        self.post("/admin/bots/wxid_ser/reminders/delete", job_id=after["job_id"])
        self.assertEqual(self.get("/admin/bots/wxid_ser/reminders")["data"]["jobs"], [])

    def test_task_belongs_to_the_bot_it_was_added_for(self):
        schedule = {"mode": "daily", "time": "09:05", "timezone": "Asia/Shanghai"}

        response = self.post("/admin/bots/wxid_ser/tasks/add", job_name="notice_moyu_schedule",
                             receivers=["群A"], schedule=schedule)

        self.assertEqual(response["code"], 0, response)
        self.assertEqual(len(self.get("/admin/bots/wxid_ser/tasks")["data"]["tasks"]), 1)
        self.assertEqual(self.get("/admin/bots/wxid_m8s/tasks")["data"]["tasks"], [])

    def test_running_a_task_now_runs_it_for_its_bot(self):
        schedule = {"mode": "daily", "time": "09:05", "timezone": "Asia/Shanghai"}
        task = self.post("/admin/bots/wxid_ser/tasks/add", job_name="notice_moyu_schedule",
                         receivers=["群A"], schedule=schedule)["data"]
        with patch.object(task_service, "_start") as start:
            self.post("/admin/bots/wxid_ser/tasks/run", task_id=task["task_id"])

        self.assertEqual(start.call_args.args[0]["bot_id"], "wxid_ser")

    def test_task_options_are_shared_by_every_bot(self):
        data = self.get("/admin/tasks/options")["data"]

        self.assertIn("notice_moyu_schedule", data["jobs"])
        self.assertIn("Asia/Shanghai", [tz["value"] for tz in data["timezones"]])


class ListenPageTests(ServerCase):
    def test_listen_page_works_on_the_base_of_the_chosen_bot(self):
        callback = self.register("wxid_ser")
        listen_store.add("wxid_ser", "群A")
        self.bases.reply(f"{callback}/execute/wx", {"code": 0, "data": [{"who": "群A"}]})
        self.bases.reply(f"{callback}/execute/batch-chat-info",
                         {"code": 0, "data": {"results": {"群A": {"success": True, "data": {"ok": 1}}}}})

        data = self.get("/admin/bots/wxid_ser/listen/status")["data"]

        self.assertEqual(data["summary"], {"healthy": 1, "unhealthy": 0})

    def test_bot_on_another_platform_has_no_listen_page(self):
        self.register("lark_app", platform="lark")

        self.assertNotEqual(self.get("/admin/bots/lark_app/listen/status")["code"], 0)


if __name__ == "__main__":
    unittest.main()
