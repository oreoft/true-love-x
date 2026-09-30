"""Someone who asks AI too often in one minute gets told once, then is only recorded until the minute is over."""

import unittest

from server_env import ServerCase
from true_love_server.core.db_engine import bot_session
from true_love_server.services import ai_rate_limit
from true_love_server.services.group_message_repository import GroupMessageRepository


class LimitTests(unittest.TestCase):
    def setUp(self):
        ai_rate_limit.reset()
        self.addCleanup(ai_rate_limit.reset)

    def ask(self, times, now=0.0, sender="alice", chat="群A", bot="wxid_m8s"):
        return [ai_rate_limit.check(bot, chat, sender, now=now) for _ in range(times)]

    def test_five_asks_a_minute_go_to_ai_then_one_notice_then_silence(self):
        self.assertEqual(self.ask(8), ["allow"] * 5 + ["notify", "drop", "drop"])

    def test_counting_starts_over_when_the_minute_is_up(self):
        self.ask(7, now=0.0)

        self.assertEqual(self.ask(1, now=60.0), ["allow"])

    def test_people_chats_and_bots_are_counted_separately(self):
        self.ask(6)

        for other in ({"sender": "bob"}, {"chat": "群B"}, {"bot": "wxid_ser"}):
            with self.subTest(**other):
                self.assertEqual(self.ask(1, **other), ["allow"])


def mention(i, sender="真爱粉"):
    return {"msg_type": "text", "msg_id": f"m{i}", "sender_id": sender, "chat_id": "委员会", "is_group": True,
            "is_at_me": True, "content": f"@kun jr 第 {i} 次"}


class OnMessageTests(ServerCase):
    def test_sixth_ask_in_a_minute_gets_a_notice_and_later_ones_are_only_stored(self):
        for i in range(1, 9):
            with self.assertNoLogs("MessageService", level="ERROR"):
                self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=mention(i))

        self.assertEqual(len(self.ai_calls), 5)
        [(url, payload)] = self.bases.sent()
        self.assertEqual((payload["sendReceiver"], payload["atReceiver"], payload["content"]),
                         ("委员会", "真爱粉", ai_rate_limit.BUSY_REPLY))
        with bot_session("wxid_ser") as db:
            self.assertEqual(len(GroupMessageRepository(db).get_messages("委员会")), 8)

    def test_plain_group_chatter_does_not_count(self):
        for i in range(1, 11):
            self.post("/base/on-message", bot=self.bot("wxid_ser"), msg={**mention(i), "is_at_me": False})

        self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=mention(99))

        self.assertEqual(len(self.ai_calls), 1)


if __name__ == "__main__":
    unittest.main()
