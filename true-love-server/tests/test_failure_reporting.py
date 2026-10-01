"""Failures are logged and reported where someone will see them, instead of being recorded as success."""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from apscheduler import events
from fastapi.testclient import TestClient

from server_env import TOKEN, ServerCase
from true_love_server import main
from true_love_server.services import base_client, bot_settings, reminder_service, scheduler_service
from true_love_server.services.base_client import _wechat
from true_love_server.services.group_message_repository import GroupMessageRepository


def message(**fields):
    return {"platform": "wechat", "msg_type": "text", "msg_id": "m1", "sender_id": "alice", "chat_id": "群A",
            "is_group": True, "content": "hi", **fields}


class BaseClientTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.callback = self.register("wxid_ser")

    def test_send_that_base_rejects_is_logged(self):
        self.bases.reply(f"{self.callback}/send/text", {"code": 102, "message": "WeChat offline"})

        with self.assertLogs("BaseClient", level="ERROR") as logs:
            ok, message_ = asyncio.run(base_client.send_text("wxid_ser", "群A", "", "hi"))

        self.assertEqual((ok, message_), (False, "WeChat offline"))
        self.assertIn("wxid_ser", logs.output[0])

    def test_sdk_failure_inside_a_successful_answer_is_a_failure(self):
        self.bases.reply(f"{self.callback}/execute/wx",
                         {"code": 0, "data": {"status": "错误", "message": "window not found", "data": None}})

        with self.assertLogs("WeChatClient", level="WARNING"):
            result = asyncio.run(base_client.wechat("wxid_ser").execute_wx("ChatWith", {"who": "群A"}))

        self.assertFalse(result["success"])
        self.assertIn("window not found", result["message"])

    def test_other_results_of_the_sdk_are_not_failures(self):
        self.assertIsNone(_wechat.sdk_failure({"status": "成功", "message": None, "data": None}))
        self.assertIsNone(_wechat.sdk_failure({"nickname": "x"}))
        self.assertIsNone(_wechat.sdk_failure(None))
        self.assertIsNotNone(_wechat.sdk_failure({"status": "失败", "message": "busy", "data": None}))


class ReminderRetryTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.callback = self.register("wxid_ser")

    def masters(self):
        return [payload["content"] for _, payload in self.bases.sent() if payload.get("is_master")]

    def test_failed_reminder_tells_the_master_and_is_retried_once(self):
        self.bases.reply(f"{self.callback}/send/text", {"code": 102, "message": "WeChat offline"})

        with self.assertLogs("ReminderService", level="ERROR"):
            reminder_service._send_reminder("群A", "alice", "喝水", "reminder_群A_1", "wxid_ser")

        [notice] = self.masters()
        self.assertIn("重试", notice)
        retry = scheduler_service.get_job("wxid_ser", "reminder_群A_1_retry")
        self.assertTrue(retry.kwargs["retried"])
        self.assertEqual(reminder_service.query_reminders("wxid_ser", "群A")[0]["job_id"], "reminder_群A_1_retry")

        # 重试也失败：再通知一次，不再排新的重试
        scheduler_service.scheduler.remove_job("reminder_群A_1_retry", jobstore="wxid_ser")
        with self.assertLogs("ReminderService", level="ERROR"):
            retry.func(**retry.kwargs)

        self.assertEqual(len(self.masters()), 2)
        self.assertIsNone(scheduler_service.get_job("wxid_ser", "reminder_群A_1_retry"))

    def test_reminder_that_raises_is_handled_like_a_failed_send(self):
        with patch.object(base_client, "send_text", Mock(side_effect=RuntimeError("boom"))), \
                self.assertLogs("ReminderService", level="ERROR"):
            reminder_service._send_reminder("群A", "", "喝水", "reminder_群A_1", "wxid_ser")

        self.assertEqual(len(self.masters()), 1)
        self.assertIsNotNone(scheduler_service.get_job("wxid_ser", "reminder_群A_1_retry"))


class SchedulerAlertTests(unittest.TestCase):
    def setUp(self):
        scheduler_service._last_alert.clear()
        patcher = patch.object(scheduler_service, "_notify_master")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def error_event(job_id="task_1", jobstore="wxid_ser"):
        return SimpleNamespace(code=events.EVENT_JOB_ERROR, job_id=job_id, jobstore=jobstore,
                               exception=RuntimeError("push failed"), traceback="Traceback ...")

    def test_failed_job_is_logged_with_its_traceback_and_sent_to_the_master_of_its_bot(self):
        with self.assertLogs("SchedulerService", level="ERROR") as logs:
            scheduler_service._scheduler_listener(self.error_event())

        self.assertIn("Traceback ...", logs.output[0])
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.args[0], "wxid_ser")

    def test_same_failure_of_the_same_job_is_sent_once_per_interval(self):
        with self.assertLogs("SchedulerService", level="ERROR"):
            for _ in range(3):
                scheduler_service._scheduler_listener(self.error_event())
            scheduler_service._scheduler_listener(self.error_event(job_id="task_2"))
            scheduler_service._scheduler_listener(SimpleNamespace(
                code=events.EVENT_JOB_MISSED, job_id="task_1", jobstore="wxid_ser",
                scheduled_run_time=datetime.now(timezone.utc)))

        self.assertEqual(self.notify.call_count, 3)

    def test_one_off_job_alerts_the_default_bot(self):
        with self.assertLogs("SchedulerService", level="ERROR"):
            scheduler_service._scheduler_listener(self.error_event(jobstore=scheduler_service.MEMORY))

        self.assertEqual(self.notify.call_args.args[0], "")

    def test_skipped_run_is_a_warning(self):
        with self.assertLogs("SchedulerService", level="WARNING"):
            scheduler_service._scheduler_listener(SimpleNamespace(
                code=events.EVENT_JOB_MAX_INSTANCES, job_id="task_1", jobstore="wxid_ser",
                scheduled_run_times=[datetime.now(timezone.utc)]))

        self.notify.assert_not_called()


class MessageFailureTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.register("wxid_ser")

    def test_failure_before_handing_to_ai_still_tells_the_user(self):
        with patch.object(bot_settings, "get_limit", Mock(side_effect=RuntimeError("db locked"))), \
                self.assertLogs("MessageService", level="ERROR"):
            self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=message(is_at_me=True))

        self.assertEqual(self.ai_calls, [])
        [(url, payload)] = self.bases.sent()
        self.assertEqual((url, payload["atReceiver"]), ("http://wxid_ser.base:5000/send/text", "alice"))

    def test_message_that_cannot_be_saved_still_goes_to_ai(self):
        with patch.object(GroupMessageRepository, "save", Mock(side_effect=RuntimeError("disk full"))), \
                self.assertLogs("MessageService", level="ERROR"):
            self.post("/base/on-message", bot=self.bot("wxid_ser"), msg=message(is_at_me=True))

        self.assertEqual(len(self.ai_calls), 1)

    def test_failed_history_query_is_an_error_not_an_empty_history(self):
        # 全局处理器回错误码；测试客户端默认把服务端异常抛出来，这里要看真实返回
        client = TestClient(self.client.app, raise_server_exceptions=False)
        with patch.object(GroupMessageRepository, "get_messages", Mock(side_effect=RuntimeError("db locked"))):
            response = client.post("/action/history", json={"token": TOKEN, "bot_id": "wxid_ser", "chat_id": "群A"})

        self.assertNotEqual(response.json()["code"], 0)


class JobRunTests(ServerCase):
    def test_running_a_job_the_bot_has_no_task_for_is_an_error(self):
        self.register("wxid_ser")

        response = self.post("/action/job/run", bot_id="wxid_ser", job_name="notice_moyu_schedule")

        self.assertNotEqual(response["code"], 0)


class StartupNoticeTests(unittest.TestCase):
    def test_notice_the_base_rejects_is_a_warning(self):
        async def rejected(bot_id, content):
            return False, "WeChat offline"

        with patch.object(main.base_client, "send_to_master", rejected), self.assertLogs("Main", level="WARNING"):
            main._notify("tl-server 启动成功")


if __name__ == "__main__":
    unittest.main()
