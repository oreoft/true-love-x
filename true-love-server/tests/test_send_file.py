"""Files reach base as URLs; base downloads them itself, so base and server need not share a disk."""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from true_love_common.http.client import HttpResult


SOURCE = Path(__file__).parents[1] / "src/true_love_server"


def module(name, path=None, **attributes):
    result = types.ModuleType(name)
    if path is not None:
        result.__path__ = [str(path)]
    result.__dict__.update(attributes)
    return result


class WeChatSendFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = types.SimpleNamespace(BASE_SERVER={"hosts": {}}, HTTP_TOKEN=["token"])
        dependencies = {
            "true_love_server": module("true_love_server", SOURCE, Config=lambda: config),
            "true_love_server.core": module("true_love_server.core", SOURCE / "core"),
            "true_love_server.services": module("true_love_server.services", SOURCE / "services"),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        self.base_client = importlib.import_module("true_love_server.services.base_client")
        wechat = importlib.import_module("true_love_server.services.base_client._wechat")
        self.post = AsyncMock(return_value=HttpResult(
            method="POST", url="", ok=True, status_code=200, data={"code": 0}))
        for patcher in (
            patch.object(wechat, "async_post", self.post),
            patch.object(wechat, "machine_bot_id", lambda: "win10-m8s"),
            patch.object(wechat, "server_host", lambda bot_id: f"http://{bot_id}-server:8088"),
            patch.object(self.base_client, "machine_bot_id", lambda: "win10-m8s"),
            patch.object(self.base_client, "bot_hosts",
                         lambda bot_id: types.SimpleNamespace(base=f"http://{bot_id}-base:5000")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def sent(self):
        return self.post.await_args.args[0], json.loads(self.post.await_args.kwargs["data"])

    async def test_file_url_is_passed_to_base_as_is(self):
        await self.base_client.send_file("http://ai.test/media/gen_img/a.jpg", "委员会")

        self.assertEqual(self.sent(), (
            "http://win10-m8s-base:5000/send/file",
            {"url": "http://ai.test/media/gen_img/a.jpg", "sendReceiver": "委员会"},
        ))

    async def test_server_image_is_offered_from_this_server(self):
        await self.base_client.get_wechat_client().send_img("moyu-jpg/2026-09-30.jpg", "委员会")

        self.assertEqual(self.sent()[1]["url"], "http://win10-m8s-server:8088/media/moyu-jpg/2026-09-30.jpg")


if __name__ == "__main__":
    unittest.main()
