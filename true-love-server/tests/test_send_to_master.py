"""Only base knows who the master of its machine is; the server asks base to deliver to that person."""

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


def base_response(data):
    return HttpResult(
        method="POST", url="http://base.test:5000/send/text", ok=True, status_code=200, headers={},
        text="", content=b"", data=data, cost_ms=1,
    )


class SendToMasterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = types.SimpleNamespace(BASE_SERVER={"hosts": {"lark": "http://lark.test"}}, HTTP_TOKEN=["token"])
        dependencies = {
            "true_love_server": module("true_love_server", SOURCE, Config=lambda: config),
            "true_love_server.core": module("true_love_server.core", SOURCE / "core"),
            "true_love_server.services": module("true_love_server.services", SOURCE / "services"),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        self.base_client = importlib.import_module("true_love_server.services.base_client")
        self.post = AsyncMock(return_value=base_response({"code": 0, "message": "success", "data": None}))
        wechat = importlib.import_module("true_love_server.services.base_client._wechat")
        for patcher in (
            patch.object(wechat, "async_post", self.post),
            patch.object(self.base_client, "machine_bot_id", lambda: "win10-m8s"),
            patch.object(self.base_client, "bot_hosts",
                         lambda bot_id: types.SimpleNamespace(base=f"http://{bot_id}-base:5000")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_notice_for_the_master_lets_base_choose_the_receiver(self):
        self.assertEqual(await self.base_client.send_to_master("deployed"), (True, ""))

        self.assertEqual(self.post.await_args.args[0], "http://win10-m8s-base:5000/send/text")
        self.assertEqual(json.loads(self.post.await_args.kwargs["data"]), {"is_master": True, "content": "deployed"})

    async def test_base_without_a_master_is_reported_to_the_caller(self):
        self.post.return_value = base_response(
            {"code": 100, "message": "No master is configured for this machine", "data": None})

        self.assertEqual(
            await self.base_client.send_to_master("deployed"),
            (False, "No master is configured for this machine"),
        )

    async def test_unreachable_base_is_reported_instead_of_raised(self):
        self.post.side_effect = ConnectionError("refused")

        with self.assertLogs("WeChatBaseClient", level="ERROR"):
            self.assertEqual(await self.base_client.send_to_master("deployed"), (False, "refused"))


if __name__ == "__main__":
    unittest.main()
