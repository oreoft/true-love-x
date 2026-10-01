"""A WeChat note in a private chat is opened and handed on as text, or as an image with its text."""

import os
import tempfile
import types
import unittest

from true_love_base.models.message_converter import convert_message


def note(get_content, *, chat_type="friend", content="笔记"):
    return types.SimpleNamespace(
        type="note", attr="friend", content=content, sender="alice", id="id-1", hash="hash-1",
        chat_info={"chat_type": chat_type, "chat_name": "alice"}, get_content=get_content,
    )


class NoteMessageTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_text_note_becomes_text_with_its_full_body(self):
        msg = convert_message(note(lambda wait=0: ["第一段", "\r", "第二段"]), "alice")

        self.assertEqual(msg.msg_type, "text")
        self.assertEqual(msg.content, "[笔记]\n第一段\n第二段")

    def test_note_with_pictures_becomes_an_image_served_from_wx_imgs(self):
        os.makedirs("download")
        picture = os.path.abspath("download/pic_1.jpg")
        open(picture, "wb").close()

        msg = convert_message(note(lambda wait=0: [picture, "配文", "\r"]), "alice")

        self.assertEqual(msg.msg_type, "image")
        self.assertEqual(msg.content, "[笔记]\n配文")
        self.assertEqual(msg.image_msg.resource.ref, "wx_imgs/pic_1.jpg")
        self.assertTrue(os.path.isfile("wx_imgs/pic_1.jpg"))

    def test_note_that_cannot_be_opened_is_left_as_it_was(self):
        failed = {"status": "失败", "message": "", "data": None}

        msg = convert_message(note(lambda wait=0: failed, content="笔记正文"), "alice")

        self.assertEqual(msg.msg_type, "note")
        self.assertEqual(msg.content, "笔记正文")
        self.assertEqual(msg.sender_id, "alice")

    def test_note_whose_window_errors_is_left_as_it_was(self):
        def broken(wait=0):
            raise RuntimeError("note window did not load")

        msg = convert_message(note(broken), "alice")

        self.assertEqual(msg.msg_type, "note")
        self.assertFalse(msg.is_group)

    def test_group_note_is_not_opened(self):
        def must_not_open(wait=0):
            raise AssertionError("group notes are not opened")

        msg = convert_message(note(must_not_open, chat_type="group", content="笔记正文"), "room")

        self.assertEqual(msg.msg_type, "note")
        self.assertTrue(msg.is_group)


if __name__ == "__main__":
    unittest.main()
