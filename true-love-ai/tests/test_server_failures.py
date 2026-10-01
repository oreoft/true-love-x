"""A server call that answers HTTP 200 with a business error is a failure, and a failed query is not "nothing found"."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from true_love_common.http.client import HttpResult

from true_love_ai.agent import server_client
from true_love_ai.agent.skill_registry import SkillFailed
from true_love_ai.agent.skills import group_context_skill, listen_skill, reminder_skill


def answer(data, ok=True, text=""):
    return HttpResult(method="POST", url="http://server.test/x", ok=ok, status_code=200 if ok else 502, headers={},
                      text=text, content=b"", data=data, cost_ms=1)


class ServerCallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = types.SimpleNamespace(http=types.SimpleNamespace(token=["token"]))
        self.post = AsyncMock()
        for patcher in (
            patch.object(server_client, "get_config", return_value=config),
            patch.object(server_client, "async_post_json", self.post),
            patch.object(server_client, "server_host", lambda: "http://server.test:8089"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_business_error_is_logged_and_its_message_kept(self):
        self.post.return_value = answer({"code": 100, "message": "job_id 不能为空", "data": None})

        with self.assertLogs("ServerClient", level="WARNING") as logs:
            result = await server_client.update_reminder("")

        self.assertEqual(result["message"], "job_id 不能为空")
        self.assertIn("path=/action/reminder/update code=100", logs.output[0])

    async def test_http_failure_comes_back_with_a_message(self):
        self.post.return_value = answer(None, ok=False, text="bad gateway")

        result = await server_client.listen_add("群A")

        self.assertEqual(result, {"code": server_client.CALL_FAILED, "message": "bad gateway"})

    async def test_a_body_that_is_not_an_object_is_a_failure(self):
        self.post.return_value = answer(None, text="<html>oops</html>")

        with self.assertLogs("ServerClient", level="WARNING"):
            self.assertFalse(await server_client.send_text("群A", "hi"))

    async def test_empty_history_and_failed_history_are_told_apart(self):
        self.post.return_value = answer({"code": 0, "data": {"messages": []}})
        self.assertEqual(await server_client.query_history("群A"), [])

        self.post.return_value = answer({"code": 500, "message": "db locked"})
        with self.assertLogs("ServerClient", level="WARNING"), self.assertRaises(server_client.ServerCallFailed):
            await server_client.query_history("群A")


class SkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_reminder_query_does_not_say_there_are_none(self):
        failed = server_client.ServerCallFailed("/action/reminder/query", {"code": -1, "message": "timeout"})
        with patch.object(server_client, "query_reminders", AsyncMock(side_effect=failed)), \
                self.assertRaises(SkillFailed) as raised:
            await reminder_skill.query_reminder({}, {"receiver": "群A"})

        self.assertIn("查询提醒失败", str(raised.exception))
        self.assertIs(raised.exception.__cause__, failed)

    async def test_no_reminders_is_still_a_normal_answer(self):
        with patch.object(server_client, "query_reminders", AsyncMock(return_value=[])):
            self.assertIn("暂未查到", await reminder_skill.query_reminder({}, {"receiver": "群A"}))

    async def test_failed_delete_says_why_instead_of_guessing_it_expired(self):
        refuse = AsyncMock(return_value={"code": 100, "message": "提醒任务不存在"})
        with patch.object(server_client, "delete_reminder", refuse), self.assertRaises(SkillFailed) as raised:
            await reminder_skill.delete_reminder({"job_id": "j1"}, {})

        self.assertIn("提醒任务不存在", str(raised.exception))
        self.assertNotIn("过期", str(raised.exception))

    async def test_listen_failure_reads_the_message_field(self):
        refuse = AsyncMock(return_value={"code": 100, "message": "找不到这个群"})
        with patch.object(server_client, "listen_add", refuse), self.assertRaises(SkillFailed) as raised:
            await listen_skill.listen_manage({"action": "add", "target": "群A"}, {})

        self.assertIn("找不到这个群", str(raised.exception))

    async def test_failed_group_history_is_not_reported_as_no_records(self):
        failed = server_client.ServerCallFailed("/action/history", {"code": -1, "message": "timeout"})
        with patch("true_love_ai.agent.skills._group_message.query_history", AsyncMock(side_effect=failed)), \
                self.assertRaises(SkillFailed) as raised:
            await group_context_skill.fetch_group_context({}, {"is_group": True, "receiver": "群A"})

        self.assertNotIn("未找到", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
