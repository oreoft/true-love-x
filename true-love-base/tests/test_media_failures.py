"""Media that cannot be fetched is flagged on the message, and a message that cannot be converted is not faked."""

import json
import os
import tempfile
import types
import unittest

from true_love_base.models.message_converter import convert_message


class Failure(dict):
    """SDK 失败时返回的 WxResponse：是 dict，布尔值为假"""

    def __init__(self, message):
        super().__init__(status="失败", message=message, data=None)

    def __bool__(self):
        return False


def raw(msg_type, *, chat_type="friend", **attributes):
    return types.SimpleNamespace(
        type=msg_type, attr="friend", content="", sender="alice", id="id-1", hash="hash-1",
        chat_info={"chat_type": chat_type, "chat_name": "alice"}, **attributes,
    )


class MediaFailureTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_downloaded_image_is_not_flagged(self):
        msg = convert_message(raw("image", download=lambda: "wx_imgs/a.jpg"), "alice")

        self.assertEqual(msg.image_msg.resource.ref, "wx_imgs/a.jpg")
        self.assertFalse(msg.media_failed)
        self.assertIs(json.loads(json.dumps(msg.to_dict()))["media_failed"], False)

    def test_image_the_sdk_reports_as_failed_is_flagged_and_logged_with_the_reason(self):
        with self.assertLogs("MessageConverter", level="WARNING") as logs:
            msg = convert_message(raw("image", download=lambda: Failure("下载超时")), "alice")

        self.assertIsNone(msg.image_msg)
        self.assertTrue(msg.media_failed)
        self.assertTrue(msg.to_dict()["media_failed"])
        self.assertIn("下载超时", logs.output[0])

    def test_file_whose_download_raises_is_flagged_with_the_stack(self):
        def broken():
            raise RuntimeError("window gone")

        with self.assertLogs("MessageConverter", level="WARNING") as logs:
            msg = convert_message(raw("file", download=broken, file_name="a.pdf"), "alice")

        self.assertIsNone(msg.file_msg.resource)
        self.assertEqual(msg.file_msg.file_name, "a.pdf")
        self.assertTrue(msg.media_failed)
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_quoted_file_that_was_never_downloaded_has_no_resource(self):
        with self.assertLogs("MessageConverter", level="WARNING"):
            msg = convert_message(raw("quote", quote_content="report.pdf", quote_nickname="bob"), "alice")

        self.assertEqual(msg.refer_msg.file_msg.file_name, "report.pdf")
        self.assertIsNone(msg.refer_msg.file_msg.resource)
        self.assertTrue(msg.refer_msg.media_failed)

    def test_quoted_file_already_in_wx_imgs_is_attached(self):
        os.makedirs("wx_imgs")
        open("wx_imgs/report.pdf", "wb").close()

        msg = convert_message(raw("quote", quote_content="report.pdf", quote_nickname="bob"), "alice")

        self.assertEqual(msg.refer_msg.file_msg.resource.ref, "wx_imgs/report.pdf")
        self.assertFalse(msg.refer_msg.media_failed)

    def test_message_that_cannot_be_converted_raises_instead_of_becoming_a_fake_private_chat(self):
        broken = types.SimpleNamespace(type="text", content="hi", chat_info=["not", "a", "dict"])

        with self.assertRaises(AttributeError):
            convert_message(broken, "room")


if __name__ == "__main__":
    unittest.main()
