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

    def test_failed_download_still_sends_the_text_and_the_other_picture(self):
        # 图没下载到不算推送失败，不抛异常
        with patch.object(job_process, "download_moyu_file", Mock(side_effect=RuntimeError("offline"))), \
                self.assertLogs("JobProcess", level="WARNING") as logs:
            job_process.notice_moyu_schedule("wxid_ser", "委员会")

        self.assertTrue(any("WARNING" in line and "moyu-jpg" in line for line in logs.output))

        self.assertEqual(len(self.texts()), 1)
        self.assertEqual([payload["url"].split("/media/")[1].split("/")[0] for payload in self.files()], ["zaobao-jpg"])

    def test_push_that_base_rejects_is_a_failure(self):
        self.bases.reply(f"{self.base}/send/text", {"code": 102, "message": "WeChat offline"})

        with self.assertLogs("JobProcess", level="ERROR"), self.assertRaisesRegex(RuntimeError, "WeChat offline"):
            job_process.notice_moyu_schedule("wxid_ser", "委员会")

        # 文字没发出去，图片照发
        self.assertEqual(len(self.files()), 2)

    def test_picture_that_base_fails_to_send_is_a_failure(self):
        self.bases.reply(f"{self.base}/send/file", {"code": 102, "message": "upload failed"})

        with self.assertLogs("JobProcess", level="ERROR"), self.assertRaisesRegex(RuntimeError, "upload failed"):
            job_process.notice_moyu_schedule("wxid_ser", "委员会")

        self.assertEqual(len(self.texts()), 1)


if __name__ == "__main__":
    unittest.main()
