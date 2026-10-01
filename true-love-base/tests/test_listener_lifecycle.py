"""Lifecycle regression tests; isolate Windows UI, HTTP and local configuration."""

import contextlib
import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).parents[1] / "src/true_love_base"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


def setup(chats, **settings):
    """server 返回的监听设置"""
    return types.SimpleNamespace(chats=chats, settings=settings)


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, SOURCE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ListenerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.sdk = Mock(nickname="test account")
        self.windows = {}
        self.sdk.AddListenChat.side_effect = self.open_chat_window
        self.sdk.StopListening.side_effect = lambda **kwargs: self.events.append("stop-sdk")
        self.sdk.GetSubWindow.side_effect = self.windows.get
        self.sdk.SendMsg.return_value = True
        self.sdk.IsOnline.return_value = True
        self.wechat_running = True
        self.server = types.SimpleNamespace(
            get_chat=Mock(), fetch_listen_chats=Mock(return_value=setup([])), use_identity=Mock())

        def open_wechat(**kwargs):
            if not self.wechat_running:
                raise RuntimeError("no logged-in WeChat main window")
            return self.sdk

        http = module(
            "true_love_base.api.server", HTTP_PORT=5000, enable_http=lambda robot: self.events.append("http"))
        dependencies = {
            "true_love_base": module("true_love_base", __path__=[]),
            "true_love_base.api": module("true_love_base.api", server=http),
            "true_love_base.wxautox4x.wxautox4x": module(
                "true_love_base.wxautox4x.wxautox4x", WeChat=open_wechat
            ),
            "wxautox4.param": module("wxautox4.param", WxParam=type("WxParam", (), {})),
            "wxautox4.uia.uiautomation": module(
                "wxautox4.uia.uiautomation", InitializeUIAutomationInCurrentThread=lambda: None
            ),
            "wxautox4.utils.lock": module(
                "wxautox4.utils.lock", ui_transaction=lambda timeout=30.0: contextlib.nullcontext()
            ),
            "true_love_common.chat_msg": module("true_love_common.chat_msg", ChatMsg=object),
            "true_love_common.observability.logging": module(
                "true_love_common.observability.logging", LoggingConfig=Mock()
            ),
            "true_love_common.observability.trace": module(
                "true_love_common.observability.trace", set_trace_id=lambda value: None
            ),
            "true_love_base.models.message_converter": module(
                "true_love_base.models.message_converter", convert_message=Mock()
            ),
            "true_love_base.utils.path_resolver": module(
                "true_love_base.utils.path_resolver", get_wx_imgs_dir=lambda: None
            ),
            "true_love_base.utils.tailnet": module("true_love_base.utils.tailnet", tailnet_ip=Mock()),
            "true_love_base.utils.win_env": module(
                "true_love_base.utils.win_env", keep_awake=lambda: None
            ),
            "true_love_base.configuration": module(
                "true_love_base.configuration",
                Config=lambda: types.SimpleNamespace(
                    master_of=lambda bot_id: "owner" if bot_id == "wxid_first" else "", callback="",
                ),
            ),
            "true_love_base.services": module(
                "true_love_base.services", __path__=[], server_client=self.server
            ),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        self.client_module = load_source("lifecycle_client", "core/wxauto_client.py")
        sys.modules["true_love_base.core"] = module(
            "true_love_base.core", WxAutoClient=self.client_module.WxAutoClient
        )
        sys.modules["true_love_base.models.reply"] = load_source("lifecycle_reply", "models/reply.py")
        self.poller_module = load_source("lifecycle_poller", "services/private_poller.py")
        sys.modules["true_love_base.services.private_poller"] = self.poller_module
        self.acceptor_module = load_source("lifecycle_acceptor", "services/friend_acceptor.py")
        sys.modules["true_love_base.services.friend_acceptor"] = self.acceptor_module
        self.robot_module = load_source("lifecycle_robot", "services/robot.py")
        sys.modules["true_love_base.services.robot"] = self.robot_module
        sys.modules["true_love_base.services.wx_supervisor"] = load_source(
            "lifecycle_supervisor", "services/wx_supervisor.py"
        )
        self.main_module = load_source("lifecycle_main", "main.py")
        for name, value in (("current_wxid", lambda: "wxid_first"), ("tailnet_ip", lambda: "100.64.0.8")):
            patcher = patch.object(self.main_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = self.client_module.WxAutoClient()
        self.assertTrue(self.client.connect())

    def open_chat_window(self, chat_name, callback=None):
        self.windows[chat_name] = Mock(who=chat_name)
        return True

    def test_cleanup_stops_sdk_once_and_preserves_open_chat_windows(self):
        self.client.cleanup()
        self.client.cleanup()

        self.sdk.StopListening.assert_called_once_with(remove=False)
        self.assertFalse(self.client.is_connected())

    def test_shutdown_rejects_new_listener_registrations(self):
        self.client.cleanup()

        self.assertFalse(self.client.add_message_listener("group", Mock()))
        self.sdk.AddListenChat.assert_not_called()

    def test_late_sdk_callback_does_not_convert_or_forward_after_cleanup(self):
        callback = Mock()
        internal = self.client._create_internal_callback("group", callback)
        self.client.cleanup()

        internal(types.SimpleNamespace(attr="friend"), object())

        self.client_module.convert_message.assert_not_called()
        callback.assert_not_called()
        self.sdk.SendMsg.assert_not_called()

    def test_cleanup_waits_for_inflight_registration_before_stopping_sdk(self):
        entered = threading.Event()
        release = threading.Event()
        stopping = threading.Event()

        def register(*args):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test registration was not released")
            self.events.append("registered")
            return self.open_chat_window(*args)

        self.sdk.AddListenChat.side_effect = register
        registering = threading.Thread(target=self.client.add_message_listener, args=("group", Mock()))

        def stop():
            stopping.set()
            self.client.cleanup()

        closing = threading.Thread(target=stop)
        registering.start()
        try:
            self.assertTrue(entered.wait(2))
            closing.start()
            self.assertTrue(stopping.wait(2))
        finally:
            release.set()
            registering.join(2)
            if closing.ident is not None:
                closing.join(2)
        self.assertFalse(registering.is_alive())
        self.assertFalse(closing.is_alive())
        self.assertEqual(self.events, ["registered", "stop-sdk"])

    def test_robot_drains_accepted_messages_and_ignores_late_callbacks(self):
        robot = self.robot_module.Robot(self.client)
        delivered = []
        robot.forward_msg = delivered.append
        first = types.SimpleNamespace(msg_hash="1", msg_id="1")
        late = types.SimpleNamespace(msg_hash="2", msg_id="2")
        self.addCleanup(robot.cleanup)

        robot.on_message(first, "group")
        robot.cleanup()
        with self.assertNoLogs("Robot", level="ERROR"):
            robot.on_message(late, "group")

        self.assertEqual(delivered, [first])

    def test_startup_cancellation_skips_remaining_chats(self):
        self.server.fetch_listen_chats.return_value = setup(["first", "second"])
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        stopped = threading.Event()

        def register(*args):
            stopped.set()
            return self.open_chat_window(*args)

        self.sdk.AddListenChat.side_effect = register
        result = robot.load_listen_chats(stop_event=stopped)

        self.assertEqual(result, {"success": ["first"], "failed": [], "unavailable": False, "settings": {"private_poll": False, "auto_accept_friends": False, "group_reply": ["at"]}})
        self.assertEqual([call.args[0] for call in self.sdk.AddListenChat.call_args_list], ["first"])

    def test_startup_cancellation_stops_registration_retries(self):
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        stopped = threading.Event()

        def register(*args):
            stopped.set()
            return False

        self.sdk.AddListenChat.side_effect = register
        with self.assertLogs("WxAutoClient", level="ERROR"), self.assertLogs("Robot", level="WARNING"):
            self.assertFalse(robot.add_listen_chat("group", stop_event=stopped))

        self.assertEqual(self.sdk.AddListenChat.call_count, 1)

    def test_nothing_is_registered_when_the_server_list_is_unavailable(self):
        self.server.fetch_listen_chats.return_value = None
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)

        result = robot.load_listen_chats(stop_event=threading.Event())

        self.assertEqual(result, {"success": [], "failed": [], "unavailable": True, "settings": {"private_poll": False, "auto_accept_friends": False, "group_reply": ["at"]}})
        self.sdk.AddListenChat.assert_not_called()

    def test_listeners_are_registered_in_the_order_the_server_returns(self):
        self.server.fetch_listen_chats.return_value = setup(["first", "second"])
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        stop = threading.Event()

        result = robot.load_listen_chats(stop_event=stop)

        self.server.fetch_listen_chats.assert_called_once_with(stop)
        self.assertEqual(result, {"success": ["first", "second"], "failed": [], "unavailable": False, "settings": {"private_poll": False, "auto_accept_friends": False, "group_reply": ["at"]}})
        self.assertEqual([call.args[0] for call in self.sdk.AddListenChat.call_args_list], ["first", "second"])

    def test_listener_is_not_registered_when_its_chat_window_never_opened(self):
        self.sdk.AddListenChat.side_effect = lambda *args: True

        with self.assertLogs("WxAutoClient", level="ERROR"):
            self.assertFalse(self.client.add_message_listener("group", Mock()))

    def test_registration_is_retried_until_the_chat_window_opens(self):
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        attempts = []

        def register(*args):
            attempts.append(args[0])
            return True if len(attempts) == 1 else self.open_chat_window(*args)

        self.sdk.AddListenChat.side_effect = register
        not_stopping = Mock()
        not_stopping.is_set.return_value = False
        not_stopping.wait.return_value = False

        with self.assertLogs("WxAutoClient", level="ERROR"), self.assertLogs("Robot", level="WARNING"):
            self.assertTrue(robot.add_listen_chat("group", stop_event=not_stopping))

        self.assertEqual(attempts, ["group", "group"])

    def run_main(
        self, *, fail_loading=False, fail_stopping=False, cancel_loading=False, wechat_running=True,
        changes=(), master="owner", unavailable=False,
    ):
        """Run main(), applying one of `changes` after each supervisor step, then shut down."""
        self.wechat_running = wechat_running
        changes = list(changes)

        def wait(timeout):
            if not changes:
                return True
            changes.pop(0)()
            return False

        client = self.client_module.WxAutoClient()
        robot = Mock(master=master)

        def load(**kwargs):
            self.events.append("load")
            if fail_loading:
                raise RuntimeError("startup failed")
            if cancel_loading:
                shutdown.is_set.return_value = True
            if unavailable:
                return {"success": [], "failed": [], "unavailable": True, "settings": {"private_poll": False, "auto_accept_friends": False, "group_reply": ["at"]}}
            return {"success": ["group"], "failed": [], "unavailable": False}

        robot.load_listen_chats.side_effect = load
        robot.cleanup.side_effect = lambda: self.events.append("drain")
        if fail_stopping:
            def stop(**kwargs):
                self.events.append("stop-sdk")
                raise RuntimeError("SDK stop failed")
            self.sdk.StopListening.side_effect = stop

        shutdown = Mock()
        shutdown.is_set.return_value = False
        shutdown.wait.side_effect = wait
        with (
            patch.object(self.main_module, "init_wx", return_value=(client, robot)),
            patch.object(self.main_module, "disable_quick_edit"),
            patch.object(self.main_module.signal, "signal"),
            patch.object(self.main_module, "Event", return_value=shutdown),
        ):
            self.main_module.main()
        self.sdk.KeepRunning.assert_not_called()
        return robot

    def test_main_initializes_once_without_keep_running_and_stops_before_draining(self):
        self.run_main()

        self.assertEqual(self.events, ["http", "load", "stop-sdk", "drain"])

    def test_cancelled_startup_does_not_announce_success(self):
        robot = self.run_main(cancel_loading=True)

        robot.send_text_msg.assert_not_called()
        self.assertEqual(self.events, ["http", "load", "stop-sdk", "drain"])

    def test_listener_setup_failure_keeps_base_alive_and_still_drains_workers(self):
        with self.assertLogs("WxSupervisor", level="ERROR"):
            robot = self.run_main(fail_loading=True)

        robot.send_text_msg.assert_not_called()
        self.assertEqual(self.events, ["http", "load", "stop-sdk", "drain"])

    def test_base_serves_http_and_stays_up_without_wechat(self):
        with self.assertLogs("WxAutoClient", level="WARNING"):
            robot = self.run_main(wechat_running=False)

        robot.load_listen_chats.assert_not_called()
        robot.send_text_msg.assert_not_called()
        self.assertEqual(self.events, ["http", "drain"])

    def go_offline(self):
        self.sdk.IsOnline.return_value = False

    def come_back(self):
        self.sdk.IsOnline.return_value = True

    def test_shutdown_notice_is_skipped_once_wechat_is_gone(self):
        with self.assertLogs("WxSupervisor", level="WARNING"):
            robot = self.run_main(changes=[self.go_offline, lambda: None])

        self.assertEqual(robot.send_text_msg.call_count, 1)
        self.assertIn("tl-base 启动成功", robot.send_text_msg.call_args.args[0])
        self.assertEqual(self.events, ["http", "load", "stop-sdk", "drain"])

    def test_master_can_tell_a_reconnect_from_a_fresh_start(self):
        with self.assertLogs("WxSupervisor", level="WARNING"):
            robot = self.run_main(changes=[self.go_offline, lambda: None, self.come_back])

        started, reconnected, stopping = [call.args[0] for call in robot.send_text_msg.call_args_list]
        self.assertIn("tl-base 启动成功", started)
        self.assertIn("重新连上微信", reconnected)
        self.assertNotIn("启动成功", reconnected)
        self.assertIn("tl-base 正在关闭", stopping)

    def test_master_is_told_when_the_server_list_is_unavailable(self):
        with self.assertLogs("Main", level="ERROR"):
            robot = self.run_main(unavailable=True)

        started = robot.send_text_msg.call_args_list[0].args[0]
        self.assertIn("没从 server 取到监听列表", started)

    def test_base_keeps_the_machine_awake_while_it_runs(self):
        with patch.object(self.main_module, "keep_awake") as keep_awake:
            self.run_main()

        keep_awake.assert_called_once_with()

    def test_messages_are_stamped_with_the_bot_this_base_runs(self):
        client, robot = self.main_module.init_wx()
        self.addCleanup(robot.cleanup)
        client.connect()
        client.add_message_listener("group", Mock())
        deliver = self.sdk.AddListenChat.call_args.args[1]

        deliver(types.SimpleNamespace(attr="friend"), object())

        self.assertEqual(self.client_module.convert_message.call_args.kwargs["bot_id"], "wxid_first")

    def test_robot_knows_the_master_of_the_account_it_finds_logged_in(self):
        client, robot = self.main_module.init_wx()
        self.addCleanup(robot.cleanup)
        self.assertEqual(robot.master, "")

        client.connect()

        self.assertEqual(robot.master, "owner")

    def test_machine_without_a_master_starts_without_announcing(self):
        with self.assertLogs("Main", level="WARNING"):
            robot = self.run_main(master="")

        robot.send_text_msg.assert_not_called()
        self.assertEqual(self.events, ["http", "load", "stop-sdk", "drain"])

    def test_missing_wechat_does_not_abort_startup(self):
        self.wechat_running = False

        with self.assertNoLogs(level="WARNING"):
            client, robot = self.main_module.init_wx()
        self.addCleanup(robot.cleanup)

        self.assertFalse(client.is_connected())

    def test_sdk_stop_failure_is_visible_and_workers_still_drain(self):
        with self.assertLogs("WxAutoClient", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "SDK stop failed"):
                self.run_main(fail_stopping=True)

        self.assertEqual(self.events, ["http", "load", "stop-sdk", "drain"])


    # ==================== 私聊轮询 ====================

    def polled(self, chat_type, *attrs, chat_name="friend"):
        self.sdk.GetNextNewMessage.return_value = {
            "chat_name": chat_name, "chat_type": chat_type,
            "msg": [types.SimpleNamespace(attr=attr, content=attr) for attr in attrs],
        }
        self.client_module.convert_message.side_effect = lambda raw, chat, **kwargs: (chat, raw.content)

    def test_poll_returns_only_what_others_sent_in_a_private_chat(self):
        self.polled("friend", "friend", "self", "system", "friend")

        self.assertEqual(self.client.next_private_messages(), [("friend", "friend"), ("friend", "friend")])
        self.sdk.GetNextNewMessage.assert_called_once_with(filter_mute=True)

    def test_poll_drops_a_chat_that_is_not_private(self):
        self.polled("group", "friend", chat_name="group")

        with self.assertLogs("WxAutoClient", level="INFO"):
            self.assertEqual(self.client.next_private_messages(), [])
        self.client_module.convert_message.assert_not_called()

    def test_poll_reports_when_nothing_is_unread(self):
        self.sdk.GetNextNewMessage.return_value = {}

        self.assertIsNone(self.client.next_private_messages())

    def test_poller_forwards_private_messages_and_keeps_polling_while_chats_are_unread(self):
        client = Mock(**{"is_connected.return_value": True})
        message = types.SimpleNamespace(chat_name="friend")
        client.next_private_messages.side_effect = [[message], [], None]
        received = []
        poller = self.poller_module.PrivatePoller(client, lambda msg, chat: received.append(chat))
        poller._enabled = True

        self.assertTrue(poller._poll_once())
        self.assertTrue(poller._poll_once())
        self.assertFalse(poller._poll_once())

        self.assertEqual(received, ["friend"])

    def test_poller_does_not_touch_wechat_when_disabled_or_offline(self):
        client = Mock(**{"is_connected.return_value": False})
        poller = self.poller_module.PrivatePoller(client, Mock())

        self.assertFalse(poller._poll_once())
        poller._enabled = True
        self.assertFalse(poller._poll_once())

        client.next_private_messages.assert_not_called()

    def test_poller_thread_stops_with_the_robot(self):
        client = Mock(**{"is_connected.return_value": True, "next_private_messages.return_value": None})
        robot = self.robot_module.Robot(self.client)
        poller = self.poller_module.PrivatePoller(client, Mock(), interval=0.01)
        robot.private_poller = poller

        poller.set_enabled(True)
        robot.cleanup()

        self.assertFalse(poller._thread.is_alive())
        self.assertTrue(client.next_private_messages.called)

    def test_server_settings_turn_features_on_and_unknown_or_missing_ones_off(self):
        self.server.fetch_listen_chats.return_value = setup(["first"], private_poll=True, someday=True)
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        robot.private_poller.set_enabled = Mock()
        robot.friend_acceptor.set_enabled = Mock()

        robot.load_listen_chats(stop_event=threading.Event())

        robot.private_poller.set_enabled.assert_called_once_with(True)
        robot.friend_acceptor.set_enabled.assert_called_once_with(False)

    def test_settings_pushed_by_the_server_change_only_what_they_name(self):
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        robot.friend_acceptor._interval = 3600
        self.sdk.GetNewFriends.return_value = []

        state = robot.apply_settings({"auto_accept_friends": True, "group_reply": ["quote", "at", "x"], "someday": 1})

        self.assertEqual(state, {"private_poll": False, "auto_accept_friends": True, "group_reply": ["at", "quote"]})

    def test_unusable_group_reply_styles_keep_the_current_ones(self):
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)

        with self.assertLogs("Robot", level="WARNING"):
            robot.apply_settings({"group_reply": ["x"]})

        self.assertEqual(robot.group_reply, ["at"])

    def test_group_reply_picks_one_of_the_chosen_styles(self):
        robot = self.robot_module.Robot(self.client)
        self.addCleanup(robot.cleanup)
        robot.client = Mock()
        robot.group_reply = ["tickle", "quote"]

        styles = set()
        for _ in range(40):
            robot.send_text_msg("hi", "room", "alice", "m1")
            styles.add(robot.client.send_text.call_args.args[4])
        robot.send_text_msg("hi", "alice")

        self.assertEqual(styles, {"tickle", "quote"})
        robot.client.send_text.assert_called_with("alice", "hi", None)

    # ==================== 自动通过好友申请 ====================

    def test_pending_friend_requests_are_accepted_and_the_chat_page_comes_back(self):
        pending = [Mock(content="alice hi"), Mock(content="bob hello")]
        pending[1].accept.side_effect = RuntimeError("button gone")
        self.sdk.GetNewFriends.return_value = pending

        with self.assertLogs("WxAutoClient", level="INFO"):
            self.assertEqual(self.client.accept_new_friends(), ["alice hi"])

        self.sdk.GetNewFriends.assert_called_once_with(acceptable=True)
        self.sdk.SwitchToChat.assert_called_once_with()

    def test_master_hears_who_was_accepted(self):
        robot = self.robot_module.Robot(self.client, master_of=lambda bot_id: "owner")
        self.addCleanup(robot.cleanup)
        robot.send_text_msg = Mock(return_value=True)
        client = Mock(**{"is_connected.return_value": True, "accept_new_friends.return_value": ["alice hi"]})
        acceptor = self.acceptor_module.FriendAcceptor(client, robot._announce_new_friends)
        acceptor._enabled = True

        acceptor._accept_once()

        robot.send_text_msg.assert_called_once_with("已自动通过 1 个好友申请：\n  1. alice hi", "owner")

    def test_acceptor_does_not_touch_wechat_when_disabled_or_offline(self):
        client = Mock(**{"is_connected.return_value": False})
        acceptor = self.acceptor_module.FriendAcceptor(client, Mock())

        acceptor._accept_once()
        acceptor._enabled = True
        acceptor._accept_once()

        client.accept_new_friends.assert_not_called()

    # ==================== 一键群免打扰 ====================

    def test_mute_all_groups_mutes_each_group_once(self):
        sessions = {name: Mock(ismute=muted) for name, muted in (("loud", False), ("quiet", True), ("stuck", False))}
        for name, session in sessions.items():
            session.name = name
        sessions["loud"].select_option.side_effect = lambda option: setattr(sessions["loud"], "ismute", True) or True
        sessions["stuck"].select_option.return_value = False
        self.sdk.GetAllRecentGroups.return_value = [("loud", 3), ("quiet", 5), ("stuck", 7), ("gone", 9)]
        self.sdk.GetSession.side_effect = lambda: list(sessions.values())

        with self.assertLogs("WxAutoClient", level="INFO"):
            result = self.client.mute_all_groups()

        self.assertEqual(result, {
            "total": 4, "muted": ["loud"], "already": ["quiet"],
            "failed": [{"chat": "stuck", "reason": "右键菜单里没有免打扰"}, {"chat": "gone", "reason": "会话列表里找不到"}],
        })
        sessions["loud"].select_option.assert_called_once_with("消息免打扰")
        sessions["quiet"].select_option.assert_not_called()
        self.sdk.ChatWith.assert_called_once_with("gone", exact=True)

    # ==================== 群回复方式 ====================

    def received(self, msg_id="m1", chat="group"):
        raw = Mock(attr="friend", id=msg_id)
        raw.exists.return_value = True
        raw.quote.return_value = True
        self.client._create_internal_callback(chat, Mock())(raw, object())
        return raw

    def test_quote_reply_quotes_a_message_still_in_the_chat_window(self):
        raw = self.received()

        with self.assertLogs("WxAutoClient", level="INFO"):
            self.assertTrue(self.client.send_text("group", "answer", ["alice"], "m1", "quote"))

        raw.quote.assert_called_once_with("answer")
        self.sdk.SendMsg.assert_not_called()

    def test_tickle_reply_pats_the_sender_then_sends_without_at(self):
        raw = self.received()
        self.client_module.TICKLE_SETTLE_SECONDS = 0

        with self.assertLogs("WxAutoClient", level="INFO"):
            self.assertTrue(self.client.send_text("group", "answer", ["alice"], "m1", "tickle"))

        raw.tickle.assert_called_once_with()
        self.sdk.SendMsg.assert_called_once_with("answer", "group", at=None)

    def test_at_reply_does_not_touch_the_message(self):
        raw = self.received()

        self.assertTrue(self.client.send_text("group", "answer", ["alice"], "m1", "at"))

        raw.quote.assert_not_called()
        raw.tickle.assert_not_called()
        self.sdk.SendMsg.assert_called_once_with("answer", "group", at=["alice"])

    def test_reply_falls_back_to_at_when_the_message_cannot_be_used(self):
        cases = {
            "unknown id": ("quote", lambda raw: "other"),
            "other chat": ("quote", lambda raw: self.client._replyable.update(m1=(0, "elsewhere", raw)) or "m1"),
            "scrolled away": ("quote", lambda raw: setattr(raw.exists, "return_value", False) or "m1"),
            "quote failed": ("quote", lambda raw: setattr(raw.quote, "return_value", False) or "m1"),
            "quote raised": ("quote", lambda raw: setattr(raw.quote, "side_effect", RuntimeError("gone")) or "m1"),
        }
        for name, (style, prepare) in cases.items():
            with self.subTest(name):
                self.sdk.SendMsg.reset_mock()
                raw = self.received()
                reply_id = prepare(raw)

                with self.assertLogs("WxAutoClient", level="INFO"):
                    self.assertTrue(self.client.send_text("group", "answer", ["alice"], reply_id, style))

                self.sdk.SendMsg.assert_called_once_with("answer", "group", at=["alice"])

    def test_tickle_reply_is_sent_without_at_even_when_the_pat_fails(self):
        cases = {
            "unknown id": lambda raw: "other",
            "tickle raised": lambda raw: setattr(raw.tickle, "side_effect", RuntimeError("gone")) or "m1",
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.sdk.SendMsg.reset_mock()
                reply_id = prepare(self.received())

                with self.assertLogs("WxAutoClient", level="INFO"):
                    self.assertTrue(self.client.send_text("group", "answer", ["alice"], reply_id, "tickle"))

                self.sdk.SendMsg.assert_called_once_with("answer", "group", at=None)

    def test_only_recent_messages_are_kept_for_replies(self):
        for i in range(self.client_module.REPLY_KEEP_COUNT + 5):
            self.received(f"m{i}")

        self.assertEqual(len(self.client._replyable), self.client_module.REPLY_KEEP_COUNT)
        self.assertNotIn("m0", self.client._replyable)

if __name__ == "__main__":
    unittest.main()
