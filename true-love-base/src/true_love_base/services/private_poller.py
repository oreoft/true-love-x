# -*- coding: utf-8 -*-
"""
PrivatePoller - 私聊轮询

没开子窗口的私聊靠主窗口的红点来收：开着时每隔几秒点开下一个有红点的会话，把私聊消息交给回调。
群消息多，走子窗口监听；群都设成免打扰后，轮询不会点开它们。开关存在 server，
base 连上微信时随监听列表一起取，后台改了开关会立即通知 base。
"""

import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Callable, Optional

from true_love_common.chat_msg import ChatMsg

if TYPE_CHECKING:
    from true_love_base.core import WxAutoClient

LOG = logging.getLogger("PrivatePoller")


class PrivatePoller:

    def __init__(self, client: "WxAutoClient", on_message: Callable[[ChatMsg, str], None], *,
                 interval: float = 2.0) -> None:
        """
        Args:
            client: 微信客户端
            on_message: 收到私聊消息时调用，参数和监听回调一样是 (消息, 聊天名)
            interval: 没有红点时隔多久再看一次（秒）
        """
        self._client = client
        self._on_message = on_message
        self._interval = interval
        self._enabled = False
        self._stop = Event()
        self._thread: Optional[Thread] = None
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """打开或关闭轮询；第一次打开时起线程，关闭后线程空转等待"""
        with self._lock:
            if enabled != self._enabled:
                LOG.info("Private poll %s", "enabled" if enabled else "disabled")
            self._enabled = enabled
            if enabled and self._thread is None and not self._stop.is_set():
                self._thread = Thread(target=self._run, name="PrivatePoller", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        """base 关闭时停止轮询，等正在处理的这一轮结束"""
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=30)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._poll_once():
                # 刚点开了一个会话，可能还有别的红点，接着看
                continue
            self._stop.wait(self._interval)

    def _poll_once(self) -> bool:
        """看一次红点，点开了会话时返回 True"""
        if not self._enabled or not self._client.is_connected():
            return False
        try:
            messages = self._client.next_private_messages()
        except Exception:
            LOG.exception("Private poll failed")
            return False
        if messages is None:
            return False
        for message in messages:
            self._on_message(message, message.chat_name)
        return True
