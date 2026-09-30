"""ChatMsg is the wire format between base, server and AI; every field must survive the trip."""

import unittest

from true_love_common.chat_msg import ChatMsg


class BotFieldsTests(unittest.TestCase):
    def test_receiving_bot_and_matched_mention_survive_serialization(self):
        sent = ChatMsg(chat_id="room", content="@kun jr hi", bot_id="wxid_abc", bot_name="kun jr", mention="@kun jr")

        received = ChatMsg.from_dict(sent.to_dict())

        self.assertEqual(received.bot_id, "wxid_abc")
        self.assertEqual(received.bot_name, "kun jr")
        self.assertEqual(received.mention, "@kun jr")

    def test_messages_from_older_senders_have_no_bot_and_no_mention(self):
        received = ChatMsg.from_dict({"chat_id": "room", "content": "hi"})

        self.assertEqual(received.bot_id, "")
        self.assertEqual(received.bot_name, "")
        self.assertEqual(received.mention, "")


if __name__ == "__main__":
    unittest.main()
