"""Services hand each other media as URLs: each one serves its own folders and resolves stored paths on demand."""

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from true_love_common.chat_msg import ChatMsg, FileMsg, ImageMsg, ResourceRef
from true_love_common.media import attach_urls, media_router, to_url


class MediaRouterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        previous = os.getcwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir, previous)
        Path("wx_imgs").mkdir()
        Path("wx_imgs/a.jpg").write_bytes(b"jpeg")
        Path("secret.txt").write_text("token")
        app = FastAPI()
        app.include_router(media_router([Path("wx_imgs")]))
        self.client = TestClient(app)

    def test_file_in_an_open_folder_is_served(self):
        response = self.client.get("/media/wx_imgs/a.jpg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"jpeg")
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_missing_file_is_not_found(self):
        self.assertEqual(self.client.get("/media/wx_imgs/b.jpg").status_code, 404)

    def test_folder_that_is_not_opened_is_not_found(self):
        self.assertEqual(self.client.get("/media/dbs/group_messages.db").status_code, 404)

    def test_path_cannot_climb_out_of_the_folder(self):
        self.assertIn(self.client.get("/media/wx_imgs/..%2Fsecret.txt").status_code, (403, 404))
        self.assertIn(self.client.get("/media/wx_imgs/%2E%2E/secret.txt").status_code, (403, 404))


class UrlTests(unittest.TestCase):
    def test_stored_path_becomes_a_url_on_the_host_that_has_it(self):
        self.assertEqual(to_url("wx_imgs/a.jpg", "http://h-m8s:5000/"), "http://h-m8s:5000/media/wx_imgs/a.jpg")

    def test_url_is_left_as_it_is(self):
        self.assertEqual(to_url("https://cdn.test/a.jpg", "http://h-m8s:5000"), "https://cdn.test/a.jpg")

    def test_every_resource_in_a_message_and_its_quote_gets_a_url(self):
        quoted = ChatMsg(msg_type="file", file_msg=FileMsg(file_name="a.pdf", resource=ResourceRef(ref="wx_imgs/a.pdf")))
        msg = ChatMsg(msg_type="image", image_msg=ImageMsg(resource=ResourceRef(ref="wx_imgs/a.jpg")), refer_msg=quoted)

        attach_urls(msg, "http://h-m8s:5000")

        self.assertEqual(msg.image_msg.resource, ResourceRef(ref="http://h-m8s:5000/media/wx_imgs/a.jpg", source="http"))
        self.assertEqual(msg.refer_msg.file_msg.resource.ref, "http://h-m8s:5000/media/wx_imgs/a.pdf")

    def test_text_message_is_untouched(self):
        msg = ChatMsg(content="hi")

        attach_urls(msg, "http://h-m8s:5000")

        self.assertEqual(msg, ChatMsg(content="hi"))


if __name__ == "__main__":
    unittest.main()
