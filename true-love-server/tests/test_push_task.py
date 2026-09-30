"""The daily push sends today's pictures, downloading them first when they are missing."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


SOURCE = Path(__file__).parents[1] / "src/true_love_server"


def module(name, path=None, **attributes):
    result = types.ModuleType(name)
    if path is not None:
        result.__path__ = [str(path)]
    result.__dict__.update(attributes)
    return result


class PushTests(unittest.TestCase):
    def setUp(self):
        config = types.SimpleNamespace(ALAPI={"token": "token"}, HTTP_TOKEN=["token"])
        self.send_text = AsyncMock(return_value=(True, ""))
        self.send_img = AsyncMock(return_value=(True, ""))
        base_client = types.SimpleNamespace(
            send_text=self.send_text, get_wechat_client=lambda: types.SimpleNamespace(send_img=self.send_img))
        dependencies = {
            "true_love_server": module("true_love_server", SOURCE),
            "true_love_server.core": module("true_love_server.core", SOURCE / "core", Config=lambda: config),
            "true_love_server.jobs": module("true_love_server.jobs", SOURCE / "jobs"),
            "true_love_server.services": module("true_love_server.services", SOURCE / "services", base_client=base_client),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        self.jobs = importlib.import_module("true_love_server.jobs.job_process")
        self.pictures = set()
        self.downloaded = []
        for patcher in (
            patch.object(self.jobs.time, "sleep"),
            patch.object(self.jobs, "_fetch_ai_data", return_value=""),
            patch.object(self.jobs, "check_image_openable", lambda path: path.split("/")[0] in self.pictures),
            patch.object(self.jobs, "download_moyu_file", lambda: self.download("moyu-jpg")),
            patch.object(self.jobs, "download_zao_bao_file", lambda: self.download("zaobao-jpg")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, folder):
        self.downloaded.append(folder)
        self.pictures.add(folder)

    def test_push_sends_the_greeting_and_both_pictures(self):
        self.jobs.notice_moyu_schedule("委员会")

        self.assertEqual(self.send_text.await_args.args[0], "委员会")
        self.assertIn("家人萌", self.send_text.await_args.args[2])
        self.assertEqual(self.send_img.await_count, 2)

    def test_us_push_uses_its_own_greeting(self):
        self.jobs.notice_usa_moyu_schedule("湾区群")

        self.assertIn("阿美莉卡", self.send_text.await_args.args[2])

    def test_missing_pictures_are_downloaded_once_for_all_pushes_of_the_day(self):
        self.jobs.notice_moyu_schedule("委员会")
        self.jobs.notice_moyu_schedule("家人群")
        self.jobs.notice_usa_moyu_schedule("湾区群")

        self.assertEqual(self.downloaded, ["moyu-jpg", "zaobao-jpg"])

    def test_failed_download_still_sends_the_text(self):
        with patch.object(self.jobs, "download_moyu_file", Mock(side_effect=RuntimeError("offline"))):
            self.jobs.notice_moyu_schedule("委员会")

        self.send_text.assert_awaited_once()
        self.send_img.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
