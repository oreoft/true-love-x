"""Messages the server did not take are kept and resent; the breaker reports opening and recovering once each."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).parents[1] / "src/true_love_base/services/server_client.py"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


def message(n):
    return types.SimpleNamespace(chat_name="room", msg_hash=f"h{n}")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ServerResendTests(unittest.TestCase):
    def setUp(self):
        self.post = Mock()
        dependencies = {
            "httpx": module("httpx", Client=Mock()),
            "true_love_common.chat_msg": module("true_love_common.chat_msg", ChatMsg=object),
            "true_love_common.hosts": module("true_love_common.hosts", SERVER_HOST="http://server.test:8089"),
            "true_love_common.http.client": module("true_love_common.http.client", post=self.post, post_json=Mock()),
            "true_love_base.configuration": module(
                "true_love_base.configuration",
                Config=lambda: types.SimpleNamespace(callback="http://100.64.0.8:5000", http_token="token")),
            "true_love_base.models.api": module(
                "true_love_base.models.api", ChatRequest=Mock(),
                ChatResponse=Mock(from_dict=lambda data: types.SimpleNamespace(is_success=data.get("code") == 0))),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location("server_resend_subject", SOURCE)
        self.client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.client)
        self.clock = Clock()
        self.sent = []
        self.server_up = True

    def send(self, msg, *, archive_only=False):
        if self.server_up:
            self.sent.append((msg.msg_hash, archive_only))
        return self.server_up

    def queue(self, **kwargs):
        result = self.client.RetryQueue(self.send, clock=self.clock, **kwargs)
        # 不起后台线程，测试里手动 flush
        result._thread = Mock()
        return result

    # ==================== 补发队列 ====================

    def test_queued_messages_are_resent_in_order_once_the_server_is_back(self):
        queue = self.queue()
        self.server_up = False
        with self.assertLogs("ServerClient", level="WARNING"):
            queue.put(message(1))
            queue.put(message(2), archive_only=True)

        self.assertFalse(queue.flush())
        self.assertEqual(len(queue), 2)

        self.server_up = True
        with self.assertLogs("ServerClient", level="INFO"):
            self.assertTrue(queue.flush())
        self.assertEqual(self.sent, [("h1", False), ("h2", True)])
        self.assertEqual(len(queue), 0)

    def test_full_queue_drops_the_oldest_with_a_warning(self):
        queue = self.queue(max_count=2)

        with self.assertLogs("ServerClient", level="WARNING") as logs:
            for n in range(3):
                queue.put(message(n))

        self.assertTrue(any("full" in line and "h0" in line for line in logs.output))
        queue.flush()
        self.assertEqual([h for h, _ in self.sent], ["h1", "h2"])

    def test_messages_older_than_the_limit_are_given_up_with_a_warning(self):
        queue = self.queue(max_age=600)
        with self.assertLogs("ServerClient", level="WARNING"):
            queue.put(message(1))
        self.clock.now = 601

        with self.assertLogs("ServerClient", level="WARNING") as logs:
            self.assertTrue(queue.flush())

        self.assertIn("h1", logs.output[0])
        self.assertEqual(self.sent, [])

    def test_unsent_messages_are_reported_at_shutdown(self):
        queue = self.queue()
        with self.assertLogs("ServerClient", level="WARNING"):
            queue.put(message(1))

        with self.assertLogs("ServerClient", level="WARNING") as logs:
            queue.stop()

        self.assertIn("h1", logs.output[0])
        self.assertEqual(len(queue), 0)

    # ==================== 熔断器 ====================

    def test_breaker_reports_opening_and_recovering_once_each(self):
        breaker = self.client.CircuitBreaker(threshold=2, reset_timeout=0)
        opened = Mock()
        breaker.on_open(opened)

        with self.assertLogs("ServerClient", level="ERROR") as logs:
            breaker.record_failure()
            breaker.record_failure()
            # 半开后又失败，不再重复报
            self.assertFalse(breaker.is_open())
            breaker.record_failure()
            breaker.record_failure()
            breaker.record_success()
            breaker.record_success()

        errors = [r.getMessage() for r in logs.records if r.levelname == "ERROR"]
        self.assertEqual(len(errors), 2)
        self.assertIn("opened", errors[0])
        self.assertIn("recovered", errors[1])
        opened.assert_called_once_with()

    # ==================== 转发 ====================

    def test_failed_forward_names_the_chat_and_message(self):
        self.post.side_effect = RuntimeError("connection refused")

        with self.assertLogs("ServerClient", level="WARNING") as logs:
            reply = self.client.get_chat(message(7))

        self.assertEqual(reply, self.client.SEND_FAILED_REPLY)
        self.assertIn("room", logs.output[0])
        self.assertIn("h7", logs.output[0])

    def test_resend_asks_for_archiving_only_when_the_user_was_told(self):
        self.post.return_value = Mock(data={"code": 0})

        self.assertTrue(self.client._post_chat(message(1), archive_only=True))

        self.assertTrue(self.client.ChatRequest.call_args.kwargs["archive_only"])


class ChatRequestTests(unittest.TestCase):
    def test_archive_only_is_sent_only_when_set(self):
        from true_love_base.models.api import ChatRequest
        from true_love_common.bot import BotInfo
        from true_love_common.chat_msg import ChatMsg

        bot = BotInfo(bot_id="bot", platform="wechat", callback="", name="")

        self.assertNotIn("archive_only", ChatRequest("t", bot, ChatMsg()).to_dict())
        self.assertTrue(ChatRequest("t", bot, ChatMsg(), archive_only=True).to_dict()["archive_only"])


if __name__ == "__main__":
    unittest.main()
