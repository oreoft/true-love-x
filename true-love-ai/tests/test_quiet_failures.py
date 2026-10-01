"""Failures that used to pass silently now leave a log line and never claim success."""

import asyncio
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from true_love_common.http.client import HttpResult

from true_love_ai import main
from true_love_ai.agent import server_client
from true_love_ai.agent.skill_registry import SkillFailed
from true_love_ai.agent.skills import dynamic_skill_manage, profile_skill, search_skill, wechat_qr_skill
from true_love_ai.api import data_routes
from true_love_ai.core import background, session as session_module
from true_love_ai.memory import memory_manager
from ai_db import memory_db


class BackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_crashing_background_task_is_logged_and_released(self):
        async def crash():
            raise RuntimeError("boom")

        with self.assertLogs("Background", level="ERROR") as logs:
            task = background.spawn(crash(), "crash")
            self.assertIn(task, background._tasks)
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        self.assertNotIn(task, background._tasks)
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_spawning_outside_a_loop_raises_without_leaking_the_coroutine(self):
        async def nothing():
            pass

        coro = nothing()
        with self.assertRaises(RuntimeError):
            background.spawn(coro, "nothing")
        self.assertIsNone(coro.cr_frame)


class CompressCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_compression_is_not_retried_on_every_message(self):
        repo = Mock(count_messages=Mock(return_value=100),
                    load=Mock(return_value=(None, [{"role": "user", "content": str(i)} for i in range(20)])))
        compress_fn = AsyncMock(side_effect=RuntimeError("model down"))
        session = session_module.Session("bot_a:room", "", compress_threshold=50, compress_keep_recent=10,
                                         compress_fn=compress_fn)

        with patch("true_love_ai.memory.session_repository.get_session_repo", return_value=repo), \
                self.assertLogs("Session", level="ERROR"):
            session.add_message("user", "1")
            await asyncio.sleep(0.01)
            session.add_message("user", "2")
            await asyncio.sleep(0.01)
        self.assertEqual(compress_fn.await_count, 1)
        repo.compress.assert_not_called()

        # 冷却过了再试，成功后正常写库
        session._compress_retry_at = datetime.now() - timedelta(seconds=1)
        compress_fn.side_effect = None
        compress_fn.return_value = "摘要"
        with patch("true_love_ai.memory.session_repository.get_session_repo", return_value=repo):
            session.add_message("user", "3")
            await asyncio.sleep(0.01)
        repo.compress.assert_called_once_with("bot_a:room", "摘要", 10)
        self.assertIsNone(session._compress_retry_at)


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_db(self)

    async def test_a_failed_write_does_not_claim_it_was_saved(self):
        ctx = {"session_id": "bot_a:room", "sender_id": "alice"}
        with patch.object(memory_manager, "upsert_user_memory", return_value=0), \
                self.assertRaises(SkillFailed) as raised:
            await profile_skill.save_user_profile({"key": "interest.music", "value": "jazz"}, ctx)

        self.assertNotIn("永久", str(raised.exception))

    async def test_a_saved_fact_is_confirmed(self):
        ctx = {"session_id": "bot_a:room", "sender_id": "alice"}
        reply = await profile_skill.save_user_profile({"key": "interest.music", "value": "jazz"}, ctx)

        self.assertIn("永久记在数据库里", reply)
        self.assertEqual(memory_manager.list_user_memory("bot_a:room", "alice")[0]["value"], "jazz")

    async def test_an_unknown_category_is_sent_back_to_the_model(self):
        reply = await profile_skill.save_user_profile({"key": "mood.today", "value": "开心"},
                                                      {"session_id": "bot_a:room", "sender_id": "alice"})

        self.assertIn("类别不对", reply)


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_curl_is_not_reported_as_no_results(self):
        failed = types.SimpleNamespace(returncode=6, stdout="", stderr="Could not resolve host")
        with patch.object(search_skill.subprocess, "run", return_value=failed), \
                self.assertLogs("SearchSkill", level="WARNING"), self.assertRaises(SkillFailed):
            await search_skill.web_search({"query": "天气"}, {})

    async def test_no_results_is_still_a_normal_answer(self):
        empty = types.SimpleNamespace(returncode=0, stdout='{"feed": {"entry": []}}', stderr="")
        with patch.object(search_skill.subprocess, "run", return_value=empty):
            self.assertIn("没有找到", await search_skill.web_search({"query": "天气"}, {}))


class DynamicSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_command_is_reported_and_not_counted(self):
        skill = {"name": "查版本", "command": "echo boom; exit 3", "parameters": ""}
        increment = Mock()
        with patch.object(dynamic_skill_manage._ss, "get_skill", return_value=skill), \
                patch.object(dynamic_skill_manage._ss, "validate_command", return_value=None), \
                patch.object(dynamic_skill_manage._ss, "increment_skill_usage", increment), \
                patch.object(dynamic_skill_manage, "check_permission", return_value=True), \
                self.assertRaises(SkillFailed) as raised:
            await dynamic_skill_manage.skill_run({"id": "check_version"}, {})

        self.assertIn("退出码 3", str(raised.exception))
        self.assertIn("boom", str(raised.exception))
        increment.assert_not_called()


class DataRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(data_routes, "verify_token", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_missing_gold_price_is_a_business_error_with_empty_text(self):
        with patch("true_love_ai.agent.skills.gold_skill.fetch_gold", AsyncMock(return_value=None)):
            response = await data_routes.get_gold()

        self.assertNotEqual(response.code, 0)
        # server 的 fetch_data 只读 data.text，空串就跳过早报里这一段
        self.assertEqual(response.data, {"text": ""})

    async def test_unsupported_currency_is_rejected(self):
        with self.assertLogs("DataRoutes", level="WARNING"):
            response = await data_routes.get_currency("btc")

        self.assertNotEqual(response.code, 0)
        self.assertEqual(response.data, {"text": ""})

    async def test_found_currency_is_returned(self):
        with patch("true_love_ai.agent.skills.currency_skill.fetch_currency", AsyncMock(return_value="美元 7.1")):
            response = await data_routes.get_currency("usd")

        self.assertEqual((response.code, response.data), (0, {"text": "美元 7.1"}))

    async def test_changed_gold_api_format_is_logged(self):
        from true_love_ai.agent.skills import gold_skill
        changed = HttpResult(method="POST", url="https://boc", ok=True, status_code=200, headers={},
                             text='{"new": 1}', content=b"", data={"new": 1}, cost_ms=1)
        with patch("true_love_common.http.client.async_post", AsyncMock(return_value=changed)), \
                self.assertLogs("GoldSkill", level="WARNING") as logs:
            self.assertIsNone(await gold_skill.fetch_gold())

        self.assertIn("status=200", logs.output[0])


class WechatQrTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_that_was_not_delivered_is_not_reported_as_sent(self):
        config = types.SimpleNamespace(nexu=types.SimpleNamespace(base_url="http://nexu.test", token=""))
        started = HttpResult(method="POST", url="http://nexu.test", ok=True, status_code=200, headers={}, text="",
                             content=b"", data={"sessionKey": "k", "qrDataUrl": "https://qr.test"}, cost_ms=1)
        spawn = Mock()
        with patch("true_love_ai.core.config.get_config", return_value=config), \
                patch("true_love_common.http.client.async_post", AsyncMock(return_value=started)), \
                patch.object(wechat_qr_skill, "_make_qr_image", return_value="a.jpg"), \
                patch.object(server_client, "send_text", AsyncMock(return_value=True)), \
                patch.object(server_client, "send_file", AsyncMock(return_value=False)), \
                patch.object(background, "spawn", spawn), \
                self.assertRaises(SkillFailed) as raised:
            await wechat_qr_skill.wechat_qr_connect({}, {"receiver": "alice"})

        self.assertNotIn("已发送", str(raised.exception))
        spawn.assert_not_called()

    async def test_failed_binding_is_told_to_the_person_who_scanned(self):
        send = AsyncMock(return_value=True)
        with patch.object(wechat_qr_skill, "_bind", AsyncMock(side_effect=RuntimeError("nexu down"))), \
                patch.object(server_client, "send_text", send), \
                self.assertLogs("WechatQrSkill", level="ERROR"):
            await wechat_qr_skill._wait_and_bind("k", "http://nexu.test", {}, "alice")

        send.assert_awaited_once_with("alice", wechat_qr_skill.BIND_FAILED_TEXT)


class MasterNoticeTests(unittest.TestCase):
    def test_a_notice_the_server_refused_is_logged(self):
        with patch.object(main, "notify_master_sync", return_value=False), \
                self.assertLogs("Main", level="WARNING") as logs:
            main.notice_master()

        self.assertIn("没发出去", logs.output[0])


if __name__ == "__main__":
    unittest.main()
