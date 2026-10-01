"""Base fetches its listeners from the server, backing off until a deadline instead of listening to nothing at once."""

import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).parents[1] / "src/true_love_base/services/server_client.py"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


def reply(data=None, *, ok=True):
    result = Mock(data=data)
    result.raise_for_status.side_effect = None if ok else RuntimeError("connection refused")
    return result


class FakeClock:
    """Stop event whose wait() advances a fake monotonic clock instead of sleeping."""

    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return False


class ListenFetchTests(unittest.TestCase):
    def setUp(self):
        self.post_json = Mock()
        dependencies = {
            "httpx": module("httpx", Client=Mock()),
            "true_love_common.chat_msg": module("true_love_common.chat_msg", ChatMsg=object),
            "true_love_common.hosts": module("true_love_common.hosts", SERVER_HOST="http://server.test:8089"),
            "true_love_common.http.client": module(
                "true_love_common.http.client", post=Mock(), post_json=self.post_json),
            "true_love_base.configuration": module(
                "true_love_base.configuration",
                Config=lambda: types.SimpleNamespace(callback="http://100.64.0.8:5000", http_token="token")),
            "true_love_base.models.api": module(
                "true_love_base.models.api", ChatRequest=Mock(), ChatResponse=Mock()),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location("listen_fetch_subject", SOURCE)
        self.client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.client)
        self.clock = FakeClock()
        clock = patch.object(self.client.time, "monotonic", self.clock.monotonic)
        clock.start()
        self.addCleanup(clock.stop)

    def test_list_is_fetched_for_this_bot_with_the_token(self):
        self.post_json.return_value = reply({"code": 0, "data": {"chats": ["群A", "好友B"], "private_poll": True}})
        self.client.use_identity(lambda: "wxid_first", lambda: "真爱粉")

        self.assertEqual(self.client.fetch_listen_chats(self.clock), (["群A", "好友B"], {"private_poll": True}))

        url, payload = self.post_json.call_args.args
        self.assertEqual(url, "http://server.test:8089/base/listen/list")
        self.assertEqual(payload, {
            "token": "token",
            "bot": {"bot_id": "wxid_first", "platform": "wechat", "callback": "http://100.64.0.8:5000", "name": "真爱粉"},
        })

    def test_retries_with_growing_delays_until_the_server_answers(self):
        self.post_json.side_effect = [reply(ok=False)] * 3 + [reply({"code": 0, "data": {"chats": ["群A"]}})]

        with self.assertLogs("ServerClient", level="WARNING"):
            self.assertEqual(self.client.fetch_listen_chats(self.clock), (["群A"], {}))

        self.assertEqual(self.clock.waits, [2, 4, 8])

    def test_gives_up_at_the_deadline(self):
        self.post_json.return_value = reply(ok=False)

        with self.assertLogs("ServerClient", level="ERROR"):
            self.assertIsNone(self.client.fetch_listen_chats(self.clock))

        self.assertEqual(self.clock.now, self.client.LISTEN_FETCH_DEADLINE)
        self.assertLessEqual(max(self.clock.waits), self.client.LISTEN_FETCH_MAX_DELAY)

    def test_only_yes_or_no_fields_count_as_switches(self):
        self.post_json.return_value = reply(
            {"code": 0, "data": {"chats": ["群A"], "auto_accept_friends": False, "note": "x"}})

        self.assertEqual(self.client.fetch_listen_chats(self.clock).switches, {"auto_accept_friends": False})

    def test_business_error_is_retried_like_a_network_error(self):
        self.post_json.side_effect = [reply({"code": 401, "message": "failed token check"}),
                                      reply({"code": 0, "data": {"chats": []}})]

        with self.assertLogs("ServerClient", level="WARNING"):
            self.assertEqual(self.client.fetch_listen_chats(self.clock), ([], {}))

    def test_shutdown_stops_the_retries(self):
        self.post_json.return_value = reply(ok=False)
        stop = threading.Event()
        stop.set()

        with self.assertLogs("ServerClient", level="WARNING"):
            self.assertIsNone(self.client.fetch_listen_chats(stop))

        self.assertEqual(self.post_json.call_count, 1)


if __name__ == "__main__":
    unittest.main()
