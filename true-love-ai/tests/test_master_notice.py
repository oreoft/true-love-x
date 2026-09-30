"""AI tells the master when it starts and stops, without knowing who the master is."""

import types
import unittest
from unittest.mock import Mock, patch

from true_love_common.http.client import HttpResult

from true_love_ai import main
from true_love_ai.agent import server_client
from true_love_ai.agent.skills import job_skill


def accepted(url):
    return HttpResult(
        method="POST", url=url, ok=True, status_code=200, headers={}, text="", content=b"",
        data={"code": 0}, cost_ms=1,
    )


class MasterNoticeTests(unittest.TestCase):
    def setUp(self):
        config = types.SimpleNamespace(http=types.SimpleNamespace(token=["token"]))
        self.post = Mock(side_effect=lambda url, payload, timeout=None: accepted(url))
        for patcher in (
            patch.object(server_client, "get_config", return_value=config),
            patch.object(server_client, "post_json", self.post),
            patch.object(server_client, "server_host", lambda: "http://server.test:8089"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_start_notice_asks_the_server_to_reach_the_master_of_the_default_bot(self):
        main.notice_master()

        url, payload = self.post.call_args.args[:2]
        self.assertEqual(url, "http://server.test:8089/action/send")
        # no bot_id: the server sends it from its default bot, as before
        self.assertEqual(payload, {"is_master": True, "content": "tl-ai 启动成功", "token": "token"})

    def test_ai_still_starts_when_the_notice_cannot_be_sent(self):
        self.post.side_effect = ConnectionError("refused")

        with self.assertLogs(level="WARNING"):
            main.notice_master()


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_the_server_cannot_find_is_reported(self):
        async def refuse(path, payload, timeout=None):
            return {"code": 400, "message": "找不到任务方法: notice_mei_yuan"}

        with patch.object(job_skill, "_async_post", side_effect=refuse):
            reply = await job_skill.run_job({"job_name": "notice_mei_yuan"}, {})

        self.assertIn("找不到任务方法", reply)

    async def test_daily_push_can_still_be_triggered_by_hand(self):
        async def accept(path, payload, timeout=None):
            return {"code": 0, "data": {"tasks": 2}}

        with patch.object(job_skill, "_async_post", side_effect=accept) as post:
            reply = await job_skill.run_job({"job_name": "notice_moyu_schedule"}, {})

        post.assert_called_once_with("/action/job/run", {"job_name": "notice_moyu_schedule"}, timeout=10.0)
        self.assertIn("已触发", reply)
        self.assertIn("2 个定时任务", reply)

    async def test_job_without_any_scheduled_task_says_where_to_add_one(self):
        async def accept(path, payload, timeout=None):
            return {"code": 0, "data": {"tasks": 0}}

        with patch.object(job_skill, "_async_post", side_effect=accept):
            reply = await job_skill.run_job({"job_name": "notice_moyu_schedule"}, {})

        self.assertIn("还没有配置定时任务", reply)


if __name__ == "__main__":
    unittest.main()
