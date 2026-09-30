"""The daily push sends today's pictures from the bot it belongs to, downloading them first when they are missing."""

import unittest
from unittest.mock import Mock, patch

from server_env import ServerCase
from true_love_server.jobs import job_process


class PushTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.base = self.register("wxid_ser")
        self.pictures = set()
        self.downloaded = []
        for patcher in (
            patch.object(job_process.time, "sleep"),
            patch.object(job_process, "fetch_data", return_value=""),
            patch.object(job_process, "check_image_openable", lambda path: path.split("/")[0] in self.pictures),
            patch.object(job_process, "download_moyu_file", lambda: self.download("moyu-jpg")),
            patch.object(job_process, "download_zao_bao_file", lambda: self.download("zaobao-jpg")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, folder):
        self.downloaded.append(folder)
        self.pictures.add(folder)

    def texts(self):
        return [payload for url, payload in self.bases.sent() if url.endswith("/send/text")]

    def files(self):
        return [payload for url, payload in self.bases.sent() if url.endswith("/send/file")]

    def test_push_sends_the_greeting_and_both_pictures_from_its_bot(self):
        job_process.notice_moyu_schedule("wxid_ser", "委员会")

        [text] = self.texts()
        self.assertEqual(text["sendReceiver"], "委员会")
        self.assertIn("家人萌", text["content"])
        self.assertEqual(len(self.files()), 2)
        self.assertTrue(all(url.startswith(self.base) for url, _ in self.bases.sent()))

    def test_pictures_are_offered_from_this_server(self):
        job_process.notice_moyu_schedule("wxid_ser", "委员会")

        urls = [payload["url"] for payload in self.files()]
        self.assertTrue(urls[0].startswith("http://localhost:8078/media/moyu-jpg/"), urls)
        self.assertTrue(urls[1].startswith("http://localhost:8078/media/zaobao-jpg/"), urls)

    def test_us_push_uses_its_own_greeting(self):
        job_process.notice_usa_moyu_schedule("wxid_ser", "湾区群")

        self.assertIn("阿美莉卡", self.texts()[0]["content"])

    def test_missing_pictures_are_downloaded_once_for_all_pushes_of_the_day(self):
        job_process.notice_moyu_schedule("wxid_ser", "委员会")
        job_process.notice_moyu_schedule("wxid_ser", "家人群")
        job_process.notice_usa_moyu_schedule("wxid_ser", "湾区群")

        self.assertEqual(self.downloaded, ["moyu-jpg", "zaobao-jpg"])

    def test_failed_download_still_sends_the_text(self):
        with patch.object(job_process, "download_moyu_file", Mock(side_effect=RuntimeError("offline"))), \
                self.assertLogs("JobProcess", level="ERROR"):
            job_process.notice_moyu_schedule("wxid_ser", "委员会")

        self.assertEqual(len(self.texts()), 1)
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main()
