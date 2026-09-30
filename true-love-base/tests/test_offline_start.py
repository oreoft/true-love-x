"""Base must outlive WeChat: start without it, connect later, notice when it drops."""

import asyncio
import importlib.util
import sys
import threading
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


SOURCE = Path(__file__).parents[1] / "src/true_love_base"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, SOURCE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def new_sdk(nickname="bot"):
    sdk = Mock(nickname=nickname)
    sdk.GetSubWindow.return_value = None
    sdk.SendMsg.return_value = True
    sdk.AddListenChat.return_value = True
    sdk.IsOnline.return_value = True
    return sdk


class WeChatDesktop:
    """Stands in for the WeChat window: absent until a test logs an account in."""

    def __init__(self):
        self.sdk = None

    def log_in(self, sdk):
        self.sdk = sdk

    def quit(self):
        self.sdk = None

    def open(self, **kwargs):
        if self.sdk is None:
            raise RuntimeError("no logged-in WeChat main window")
        return self.sdk


def load_client_module(desktop, real_converter=False):
    """Load the real client with only the Windows SDK replaced; optionally keep the real message converter."""
    dependencies = {
        "true_love_base.wxautox4x.wxautox4x": module("true_love_base.wxautox4x.wxautox4x", WeChat=desktop.open),
        "wxautox4.param": module("wxautox4.param", WxParam=type("WxParam", (), {})),
        "true_love_base.utils.path_resolver": module(
            "true_love_base.utils.path_resolver", get_wx_imgs_dir=lambda: None, to_server_path=lambda path: path
        ),
    }
    if not real_converter:
        dependencies["true_love_common.chat_msg"] = module("true_love_common.chat_msg", ChatMsg=object)
        dependencies["true_love_base.models.message_converter"] = module(
            "true_love_base.models.message_converter", convert_message=Mock()
        )
    with patch.dict(sys.modules, dependencies):
        return load_source("offline_start_client", "core/wxauto_client.py")


class OfflineClientTests(unittest.TestCase):
    def setUp(self):
        self.desktop = WeChatDesktop()
        self.client = load_client_module(self.desktop).WxAutoClient()

    def test_client_is_created_offline_when_wechat_is_not_running(self):
        self.assertFalse(self.client.is_connected())

    def test_connect_keeps_failing_until_wechat_is_logged_in(self):
        with self.assertLogs("WxAutoClient", level="WARNING"):
            self.assertFalse(self.client.connect())
        self.assertFalse(self.client.is_connected())

        self.desktop.log_in(new_sdk())

        self.assertTrue(self.client.connect())
        self.assertTrue(self.client.is_connected())

    def test_wechat_window_that_is_not_online_is_not_adopted(self):
        sdk = new_sdk()
        sdk.IsOnline.return_value = False
        self.desktop.log_in(sdk)

        with self.assertLogs("WxAutoClient", level="WARNING"):
            self.assertFalse(self.client.connect())
            self.assertFalse(self.client.send_text("alice", "hi"))

        self.assertFalse(self.client.is_connected())
        sdk.SendMsg.assert_not_called()

    def test_wechat_logged_in_to_another_account_is_not_adopted(self):
        client = load_client_module(self.desktop).WxAutoClient(bot_id="wxid_m8s", account_of=lambda: "wxid_other")
        self.desktop.log_in(new_sdk())

        with self.assertLogs("WxAutoClient", level="WARNING") as logs:
            self.assertFalse(client.connect())

        self.assertFalse(client.is_connected())
        self.assertIn("wxid_other", logs.output[0])

    def test_wechat_logged_in_to_this_bot_is_adopted(self):
        client = load_client_module(self.desktop).WxAutoClient(bot_id="wxid_m8s", account_of=lambda: "wxid_m8s")
        self.desktop.log_in(new_sdk())

        self.assertTrue(client.connect())

    def test_repeated_connect_failures_are_reported_once(self):
        with self.assertLogs("WxAutoClient", level="WARNING") as logs:
            self.client.connect()
            self.client.connect()
            self.client.connect()

        self.assertEqual(len(logs.records), 1)

    def test_a_new_outage_is_reported_again(self):
        with self.assertLogs("WxAutoClient", level="WARNING"):
            self.client.connect()
        self.desktop.log_in(new_sdk())
        self.client.connect()
        self.client.disconnect()
        self.desktop.quit()

        with self.assertLogs("WxAutoClient", level="WARNING") as logs:
            self.client.connect()
            self.client.connect()

        self.assertEqual(len(logs.records), 1)

    def test_cleanup_while_offline_has_no_sdk_to_stop(self):
        with self.assertNoLogs("WxAutoClient", level="ERROR"):
            self.client.cleanup()

        self.assertFalse(self.client.is_connected())


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.desktop = WeChatDesktop()
        self.first = new_sdk()
        self.desktop.log_in(self.first)
        self.client = load_client_module(self.desktop).WxAutoClient()
        self.assertTrue(self.client.connect())

    def test_disconnect_stops_the_stale_listener_and_goes_offline(self):
        self.client.disconnect()

        self.first.StopListening.assert_called_once_with(remove=False)
        self.assertFalse(self.client.is_connected())

    def test_sending_while_disconnected_fails_without_touching_the_stale_window(self):
        self.client.disconnect()

        with self.assertLogs("WxAutoClient", level="ERROR"):
            self.assertFalse(self.client.send_text("alice", "hi"))

        self.first.SendMsg.assert_not_called()

    def test_reconnect_sends_through_the_new_wechat_window(self):
        self.client.disconnect()
        second = new_sdk()
        self.desktop.log_in(second)

        self.assertTrue(self.client.connect())
        self.assertTrue(self.client.send_text("alice", "hi"))

        second.SendMsg.assert_called_once_with("hi", "alice", at=None)
        self.first.SendMsg.assert_not_called()

    def test_disconnect_survives_a_dead_wechat_that_cannot_stop_listening(self):
        self.first.StopListening.side_effect = RuntimeError("window is gone")

        with self.assertLogs("WxAutoClient", level="WARNING"):
            self.client.disconnect()

        self.assertFalse(self.client.is_connected())


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        self.desktop = WeChatDesktop()
        self.sdk = new_sdk()
        self.desktop.log_in(self.sdk)
        self.client = load_client_module(self.desktop).WxAutoClient()

    def test_client_that_never_connected_is_offline(self):
        self.assertFalse(self.client.check_online())

        self.sdk.IsOnline.assert_not_called()

    def test_logged_in_wechat_is_online(self):
        self.client.connect()

        self.assertTrue(self.client.check_online())

    def test_logged_out_wechat_is_offline(self):
        self.client.connect()
        self.sdk.IsOnline.return_value = False

        self.assertFalse(self.client.check_online())

    def test_wechat_window_that_died_is_offline(self):
        self.client.connect()
        self.sdk.IsOnline.side_effect = RuntimeError("window is gone")

        with self.assertLogs("WxAutoClient", level="WARNING"):
            self.assertFalse(self.client.check_online())


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.desktop = WeChatDesktop()
        client_module = load_client_module(self.desktop)
        self.now = datetime(2026, 9, 29, 8, 0, 0)
        clock = patch.object(client_module, "datetime", Mock(now=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)
        self.client = client_module.WxAutoClient(bot_id="win10-m8s")

    def connect_at(self, moment, nickname="真爱粉"):
        self.desktop.log_in(new_sdk(nickname))
        self.now = moment
        self.assertTrue(self.client.connect())

    def test_offline_status_dates_back_to_startup(self):
        self.now = datetime(2026, 9, 29, 8, 30, 0)

        self.assertEqual(
            self.client.status(),
            {"wx_online": False, "bot_id": "win10-m8s", "self_name": None, "since": "2026-09-29T08:00:00"},
        )

    def test_failed_connect_attempts_do_not_restart_the_offline_clock(self):
        self.now = datetime(2026, 9, 29, 8, 10, 0)
        with self.assertLogs("WxAutoClient", level="WARNING"):
            self.client.connect()

        self.assertEqual(self.client.status()["since"], "2026-09-29T08:00:00")

    def test_online_status_names_the_account_and_when_it_connected(self):
        self.connect_at(datetime(2026, 9, 29, 8, 5, 0))
        self.now = datetime(2026, 9, 29, 9, 0, 0)

        self.assertEqual(
            self.client.status(),
            {"wx_online": True, "bot_id": "win10-m8s", "self_name": "真爱粉", "since": "2026-09-29T08:05:00"},
        )

    def test_status_after_a_drop_shows_when_wechat_went_away(self):
        self.connect_at(datetime(2026, 9, 29, 8, 5, 0))
        self.now = datetime(2026, 9, 29, 8, 20, 0)
        self.client.disconnect()

        self.assertEqual(
            self.client.status(),
            {"wx_online": False, "bot_id": "win10-m8s", "self_name": None, "since": "2026-09-29T08:20:00"},
        )

    def test_reconnecting_as_another_account_reports_the_new_name(self):
        self.connect_at(datetime(2026, 9, 29, 8, 5, 0))
        self.assertEqual(self.client.status()["self_name"], "真爱粉")
        self.client.disconnect()

        self.connect_at(datetime(2026, 9, 29, 8, 40, 0), nickname="小号")

        self.assertEqual(
            self.client.status(),
            {"wx_online": True, "bot_id": "win10-m8s", "self_name": "小号", "since": "2026-09-29T08:40:00"},
        )


def group_message(content):
    return types.SimpleNamespace(
        type="text", attr="friend", content=content, sender="alice", id="id-1", hash="hash-1",
        chat_info={"chat_type": "group", "chat_name": "room"},
    )


class IdentityTests(unittest.TestCase):
    """Messages are judged against the account that is logged in, and carry the bot that received them."""

    def setUp(self):
        self.desktop = WeChatDesktop()
        self.client = load_client_module(self.desktop, real_converter=True).WxAutoClient(bot_id="win11-ser")
        self.received = []

    def log_in_and_listen(self, nickname):
        sdk = new_sdk(nickname)
        sdk.GetSubWindow.return_value = Mock(who="room")
        self.desktop.log_in(sdk)
        self.assertTrue(self.client.connect())
        self.assertTrue(self.client.add_message_listener("room", lambda msg, chat: self.received.append(msg)))
        return sdk.AddListenChat.call_args.args[1]

    def test_forwarded_message_names_the_bot_that_received_it(self):
        deliver = self.log_in_and_listen("kun jr")

        deliver(group_message("hello"), Mock(who="room"))

        self.assertEqual(self.received[0].bot_id, "win11-ser")
        self.assertFalse(self.received[0].is_at_me)

    def test_mention_of_the_logged_in_nickname_is_for_this_bot(self):
        deliver = self.log_in_and_listen("kun jr")

        deliver(group_message("@kun jr\u2005hi"), Mock(who="room"))

        self.assertTrue(self.received[0].is_at_me)
        self.assertEqual(self.received[0].mention, "@kun jr")

    def test_mentions_follow_the_account_after_logging_in_as_someone_else(self):
        self.log_in_and_listen("kun jr")
        self.client.disconnect()
        deliver = self.log_in_and_listen("小号")

        deliver(group_message("@kun jr\u2005hi"), Mock(who="room"))
        deliver(group_message("@小号\u2005hi"), Mock(who="room"))

        self.assertEqual([m.bot_id for m in self.received], ["win11-ser", "win11-ser"])
        self.assertEqual([m.is_at_me for m in self.received], [False, True])


class Timeline:
    """A stop event that changes the world between supervisor steps, then asks it to stop."""

    def __init__(self, *changes):
        self.changes = list(changes)
        self.waits = []

    def is_set(self):
        return False

    def wait(self, timeout):
        self.waits.append(timeout)
        if not self.changes:
            return True
        self.changes.pop(0)()
        return False


def nothing_happens():
    pass


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.desktop = WeChatDesktop()
        self.sdk = new_sdk()
        self.client = load_client_module(self.desktop).WxAutoClient()
        self.supervisor_module = load_source("offline_start_supervisor", "services/wx_supervisor.py")
        self.started_listening = Mock()

    def supervise(self, *changes, stop_event=None):
        timeline = stop_event or Timeline(*changes)
        self.supervisor_module.WxSupervisor(
            self.client, self.started_listening, timeline,
            reconnect_interval=1, health_interval=5, offline_threshold=2,
        ).run()
        return timeline

    def go_offline(self):
        self.sdk.IsOnline.return_value = False

    def come_back(self):
        self.sdk.IsOnline.return_value = True

    def test_keeps_retrying_until_wechat_appears_then_starts_listening(self):
        with self.assertLogs("WxAutoClient", level="WARNING"):
            timeline = self.supervise(nothing_happens, lambda: self.desktop.log_in(self.sdk))

        self.assertTrue(self.client.is_connected())
        self.started_listening.assert_called_once_with()
        self.assertEqual(timeline.waits, [1, 1, 5])

    def test_one_failed_check_does_not_drop_the_connection(self):
        self.desktop.log_in(self.sdk)

        with self.assertLogs("WxSupervisor", level="WARNING"):
            self.supervise(self.go_offline, self.come_back, nothing_happens)

        self.assertTrue(self.client.is_connected())
        self.sdk.StopListening.assert_not_called()
        self.started_listening.assert_called_once_with()

    def test_failed_checks_must_be_consecutive_to_drop_the_connection(self):
        self.desktop.log_in(self.sdk)

        with self.assertLogs("WxSupervisor", level="WARNING"):
            self.supervise(self.go_offline, self.come_back, self.go_offline)

        self.assertTrue(self.client.is_connected())
        self.sdk.StopListening.assert_not_called()

    def test_wechat_that_stays_offline_is_dropped_and_listening_restarts_on_the_new_login(self):
        self.desktop.log_in(self.sdk)
        relogin = new_sdk()

        with self.assertLogs("WxSupervisor", level="WARNING"):
            timeline = self.supervise(
                self.go_offline, nothing_happens, lambda: self.desktop.log_in(relogin),
            )

        self.sdk.StopListening.assert_called_once_with(remove=False)
        self.assertEqual(self.started_listening.call_count, 2)
        self.assertEqual(timeline.waits, [5, 5, 1, 5])
        self.assertTrue(self.client.send_text("alice", "hi"))
        relogin.SendMsg.assert_called_once_with("hi", "alice", at=None)

    def test_failing_to_start_listening_drops_the_connection_and_retries(self):
        self.desktop.log_in(self.sdk)
        self.started_listening.side_effect = [RuntimeError("cannot open chat windows"), None]

        with self.assertLogs("WxSupervisor", level="ERROR"):
            timeline = self.supervise(nothing_happens)

        self.sdk.StopListening.assert_called_once_with(remove=False)
        self.assertEqual(self.started_listening.call_count, 2)
        self.assertTrue(self.client.is_connected())
        self.assertEqual(timeline.waits, [1, 5])

    def test_shutdown_requested_before_start_never_touches_wechat(self):
        self.desktop.log_in(self.sdk)
        stopped = threading.Event()
        stopped.set()

        self.supervise(stop_event=stopped)

        self.assertFalse(self.client.is_connected())
        self.started_listening.assert_not_called()


WECHAT_OFFLINE = {"code": 101, "message": "WeChat offline", "data": None}


class RoutesTests(unittest.TestCase):
    def setUp(self):
        from true_love_base.api import routes

        self.routes = routes
        self.desktop = WeChatDesktop()
        self.sdk = new_sdk("真爱粉")
        client_module = load_client_module(self.desktop)
        self.now = datetime(2026, 9, 29, 8, 0, 0)
        clock = patch.object(client_module, "datetime", Mock(now=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)
        self.client = client_module.WxAutoClient(bot_id="win10-m8s")
        self.robot = types.SimpleNamespace(
            client=self.client,
            master="owner",
            send_text_msg=Mock(return_value=True),
            send_file_msg=Mock(return_value=True),
            add_listen_chat=Mock(return_value=True),
        )
        robot = patch.object(routes, "_get_robot", return_value=self.robot)
        robot.start()
        self.addCleanup(robot.stop)

    def log_in(self):
        self.desktop.log_in(self.sdk)
        self.now = datetime(2026, 9, 29, 8, 5, 0)
        self.assertTrue(self.client.connect())

    def test_every_wechat_endpoint_reports_offline_instead_of_trying(self):
        requests = {
            "send_text": {"sendReceiver": "alice", "content": "hi"},
            "send_file": {"sendReceiver": "alice", "url": "http://h-ser:8088/media/gen_img/missing.png"},
            "add_listen": {"nickname": "alice"},
            "execute_wx": {"name": "GetMyInfo"},
            "execute_chat": {"chat_name": "alice", "name": "ChatInfo"},
            "batch_chat_info": {"chat_names": ["alice"]},
        }
        for endpoint, body in requests.items():
            with self.subTest(endpoint=endpoint):
                response = asyncio.run(getattr(self.routes, endpoint)(body))

                self.assertEqual(response, WECHAT_OFFLINE)
        self.robot.send_text_msg.assert_not_called()
        self.robot.send_file_msg.assert_not_called()
        self.robot.add_listen_chat.assert_not_called()

    def test_endpoints_refuse_again_after_wechat_drops(self):
        self.log_in()
        self.client.disconnect()

        response = asyncio.run(self.routes.send_text({"sendReceiver": "alice", "content": "hi"}))

        self.assertEqual(response, WECHAT_OFFLINE)
        self.robot.send_text_msg.assert_not_called()

    def test_text_is_sent_once_wechat_is_connected(self):
        self.log_in()

        response = asyncio.run(self.routes.send_text({"sendReceiver": "alice", "content": "hi"}))

        self.assertEqual(response, {"code": 0, "message": "success", "data": None})
        self.robot.send_text_msg.assert_called_once_with("hi", "alice", None)

    def test_text_for_the_master_goes_to_the_master_of_this_bot(self):
        self.log_in()

        response = asyncio.run(self.routes.send_text({"is_master": True, "content": "deployed"}))

        self.assertEqual(response, {"code": 0, "message": "success", "data": None})
        self.robot.send_text_msg.assert_called_once_with("deployed", "owner", None)

    def test_chat_that_happens_to_be_called_master_is_an_ordinary_receiver(self):
        self.log_in()

        asyncio.run(self.routes.send_text({"sendReceiver": "master", "content": "hi"}))

        self.robot.send_text_msg.assert_called_once_with("hi", "master", None)

    def test_file_for_the_master_goes_to_the_master_of_this_bot(self):
        self.log_in()

        with patch.object(self.routes, "download", AsyncMock(return_value="send-files/report.png")) as download:
            response = asyncio.run(self.routes.send_file({"is_master": True, "url": "http://h-ser:8088/media/gen_img/report.png"}))

        self.assertEqual(response, {"code": 0, "message": "success", "data": None})
        download.assert_awaited_once_with("http://h-ser:8088/media/gen_img/report.png", self.routes.SEND_FILES_DIR)
        self.robot.send_file_msg.assert_called_once_with("send-files/report.png", "owner")

    def test_file_that_cannot_be_downloaded_is_reported_as_not_sent(self):
        self.log_in()

        with patch.object(self.routes, "download", AsyncMock(side_effect=RuntimeError("HTTP 404"))):
            response = asyncio.run(self.routes.send_file({"sendReceiver": "group", "url": "http://h-ser:8088/media/gen_img/x.png"}))

        self.assertEqual(response, self.routes.ApiErrors.SEND_FAILED.to_dict())
        self.robot.send_file_msg.assert_not_called()

    def test_message_for_the_master_is_refused_for_a_bot_without_one(self):
        self.log_in()
        self.robot.master = ""

        response = asyncio.run(self.routes.send_text({"is_master": True, "content": "deployed"}))

        self.assertEqual(response, {"code": 100, "message": "No master is configured for this bot", "data": None})
        self.robot.send_text_msg.assert_not_called()

    def test_status_reports_wechat_offline_since_startup(self):
        response = asyncio.run(self.routes.status())

        self.assertEqual(response, {
            "code": 0,
            "message": "success",
            "data": {"wx_online": False, "bot_id": "win10-m8s", "self_name": None, "since": "2026-09-29T08:00:00"},
        })

    def test_status_reports_the_connected_account(self):
        self.log_in()

        response = asyncio.run(self.routes.status())

        self.assertEqual(response, {
            "code": 0,
            "message": "success",
            "data": {
                "wx_online": True, "bot_id": "win10-m8s", "self_name": "真爱粉", "since": "2026-09-29T08:05:00",
            },
        })

    def test_ping_still_answers_while_wechat_is_offline(self):
        self.assertEqual(asyncio.run(self.routes.ping()), "pong")


if __name__ == "__main__":
    unittest.main()
