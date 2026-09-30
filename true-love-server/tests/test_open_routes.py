"""Outside callers push notices to the master; without a bot they go out through the default bot, as before."""

import unittest

from server_env import DEFAULT_BOT_ID, ServerCase


class SendMsgTests(ServerCase):
    def test_push_goes_to_the_master_of_the_default_bot(self):
        default = self.register(DEFAULT_BOT_ID)
        self.register("wxid_ser")

        response = self.post("/send-msg", sendReceiver="master", content="deployed")

        self.assertEqual(response["code"], 0)
        self.assertEqual(self.bases.sent(), [(f"{default}/send/text", {"is_master": True, "content": "deployed"})])

    def test_push_can_name_another_bot(self):
        self.register(DEFAULT_BOT_ID)
        ser = self.register("wxid_ser")

        self.post("/send-msg", sendReceiver="master", content="deployed", bot_id="wxid_ser")

        self.assertEqual(self.bases.sent(), [(f"{ser}/send/text", {"is_master": True, "content": "deployed"})])

    def test_push_to_anyone_else_or_without_content_is_refused(self):
        self.register(DEFAULT_BOT_ID)

        self.assertNotEqual(self.post("/send-msg", sendReceiver="alice", content="hi")["code"], 0)
        self.assertNotEqual(self.post("/send-msg", sendReceiver="master")["code"], 0)
        self.assertEqual(self.bases.sent(), [])

    def test_push_before_the_default_bot_registered_is_reported(self):
        response = self.post("/send-msg", sendReceiver="master", content="deployed")

        self.assertIn(DEFAULT_BOT_ID, response["message"])

    def test_push_without_a_configured_default_bot_says_so(self):
        from unittest.mock import patch
        from true_love_server.core import Config

        self.register(DEFAULT_BOT_ID)
        with patch.object(Config(), "DEFAULT_BOT_ID", ""):
            response = self.post("/send-msg", sendReceiver="master", content="deployed")

        self.assertIn("default_bot_id", response["message"])
        self.assertEqual(self.bases.sent(), [])

    def test_push_needs_the_token(self):
        self.register(DEFAULT_BOT_ID)

        self.assertNotEqual(self.post("/send-msg", token="wrong", sendReceiver="master", content="x")["code"], 0)


if __name__ == "__main__":
    unittest.main()
