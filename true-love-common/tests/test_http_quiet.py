"""Quiet calls (tl-admin traffic) log nothing unless they fail."""

import unittest
from unittest.mock import patch

import httpx

from true_love_common.http import client


def transport(status=200):
    return httpx.MockTransport(lambda request: httpx.Response(status, json={"code": 0}))


class QuietTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, url, **kwargs):
        real = httpx.AsyncClient
        with patch.object(client.httpx, "AsyncClient", lambda **kw: real(transport=transport(), **kw)):
            return await client.async_post_json(url, {"token": "t"}, **kwargs)

    async def test_normal_calls_log_start_and_end(self):
        with self.assertLogs(client.LOG, level="INFO") as logs:
            await self.call("http://ai.test/admin/skill/list")
        self.assertEqual(len(logs.records), 2)

    async def test_quiet_calls_log_nothing_when_they_succeed(self):
        with self.assertNoLogs(client.LOG, level="INFO"):
            result = await self.call("http://ai.test/admin/skill/list", quiet=True)
        self.assertTrue(result.ok)

    async def test_quiet_calls_still_log_failures(self):
        with patch.object(client.httpx, "AsyncClient", side_effect=httpx.ConnectError("down")):
            with self.assertLogs(client.LOG, level="ERROR"):
                result = await client.async_post_json("http://ai.test/admin/skill/list", {}, quiet=True)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
