"""Base reports who it is on every call to the server."""

import unittest

from true_love_common.bot import BotInfo


class BotInfoTests(unittest.TestCase):
    def test_round_trip(self):
        info = BotInfo(bot_id="wxid_a", platform="wechat", callback="http://100.1.2.3:5000", name="真爱粉")

        self.assertEqual(BotInfo.from_dict(info.to_dict()), info)

    def test_callback_is_stored_without_a_trailing_slash(self):
        info = BotInfo.from_dict({"bot_id": "wxid_a", "platform": "wechat", "callback": "http://100.1.2.3:5000/"})

        self.assertEqual(info.callback, "http://100.1.2.3:5000")

    def test_missing_fields_are_named(self):
        with self.assertRaisesRegex(ValueError, "platform, callback"):
            BotInfo.from_dict({"bot_id": "wxid_a"})

    def test_anything_but_a_dict_is_missing_everything(self):
        with self.assertRaisesRegex(ValueError, "bot_id"):
            BotInfo.from_dict(None)


if __name__ == "__main__":
    unittest.main()
