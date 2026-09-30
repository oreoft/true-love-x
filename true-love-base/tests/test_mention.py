"""In a group the bot answers only messages that call it by its own account nickname."""

import types
import unittest

from true_love_base.models.message_converter import convert_message


def message(content, *, chat_type="group", msg_type="text"):
    raw = types.SimpleNamespace(
        type=msg_type, attr="friend", content=content, sender="alice", id="id-1", hash="hash-1",
        chat_info={"chat_type": chat_type, "chat_name": "room"},
    )
    if msg_type == "voice":
        raw.to_text = lambda: content
    return raw


class MentionTests(unittest.TestCase):
    def convert(self, raw, **bot):
        bot = {"bot_id": "win11-ser", "bot_name": "kun jr", **bot}
        return convert_message(raw, "room", **bot)

    def test_group_message_mentioning_the_account_nickname_is_for_the_bot(self):
        msg = self.convert(message("@kun jr hello"))

        self.assertTrue(msg.is_at_me)
        self.assertEqual(msg.mention, "@kun jr")

    def test_mention_of_another_member_is_not_for_the_bot(self):
        msg = self.convert(message("@真爱粉 hello"))

        self.assertFalse(msg.is_at_me)
        self.assertEqual(msg.mention, "")

    def test_nickname_without_the_at_sign_is_only_talk_about_the_bot(self):
        msg = self.convert(message("kun jr is a bot"))

        self.assertFalse(msg.is_at_me)
        self.assertEqual(msg.mention, "")

    def test_voice_message_calls_the_bot_by_name_because_speech_has_no_at_sign(self):
        msg = self.convert(message("kun jr what time is it", msg_type="voice"))

        self.assertTrue(msg.is_at_me)
        self.assertEqual(msg.mention, "kun jr")

    def test_no_word_calls_the_bot_except_its_nickname(self):
        msg = self.convert(message("zaf 帮我查一下"), bot_name="真爱粉")

        self.assertFalse(msg.is_at_me)
        self.assertEqual(msg.mention, "")

    def test_private_message_is_not_a_group_mention_but_still_reports_the_call(self):
        msg = self.convert(message("@kun jr hi", chat_type="friend"))

        self.assertFalse(msg.is_at_me)
        self.assertEqual(msg.mention, "@kun jr")

    def test_bot_that_does_not_know_its_nickname_answers_no_mentions(self):
        msg = self.convert(message("@someone hello"), bot_name="")

        self.assertFalse(msg.is_at_me)
        self.assertEqual(msg.mention, "")

    def test_every_message_names_the_machine_that_received_it(self):
        msg = self.convert(message("hello"))

        self.assertEqual(msg.bot_id, "win11-ser")

    def test_every_message_carries_the_account_nickname_for_the_ai(self):
        self.assertEqual(self.convert(message("hello")).bot_name, "kun jr")
        self.assertEqual(self.convert(object()).bot_name, "kun jr")


if __name__ == "__main__":
    unittest.main()
