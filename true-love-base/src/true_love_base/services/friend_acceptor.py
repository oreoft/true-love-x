# -*- coding: utf-8 -*-
"""
FriendAcceptor - 自动通过好友申请

开着时每隔几分钟看一次通讯录里的新朋友，有待通过的申请就通过，再告诉管理员通过了谁。
开关存在 server，base 连上微信时随监听列表一起取，后台改了开关会立即通知 base。
"""

import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from true_love_base.core import WxAutoClient

LOG = logging.getLogger("FriendAcceptor")


class FriendAcceptor:

    def __init__(self, client: "WxAutoClient", on_accepted: Callable[[list[str]], None], *,
                 interval: float = 120.0) -> None:
        """
        Args:
            client: 微信客户端
            on_accepted: 这一轮通过了申请时调用，参数是申请人
            interval: 隔多久看一次（秒）；每次都要切到通讯录页再切回来
        """
        self._client = client
        self._on_accepted = on_accepted
        self._interval = interval
        self._enabled = False
        self._stop = Event()
        self._thread: Optional[Thread] = None
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """打开或关闭；第一次打开时起线程，关闭后线程空转等待"""
        with self._lock:
            if enabled != self._enabled:
                LOG.info("Auto accepting friend requests %s", "enabled" if enabled else "disabled")
            self._enabled = enabled
            if enabled and self._thread is None and not self._stop.is_set():
                self._thread = Thread(target=self._run, name="FriendAcceptor", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        """base 关闭时停止，等正在处理的这一轮结束"""
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=30)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._accept_once()
            self._stop.wait(self._interval)

    def _accept_once(self) -> None:
        if not self._enabled or not self._client.is_connected():
            return
        try:
            accepted = self._client.accept_new_friends()
        except Exception:
            LOG.exception("Failed to accept friend requests")
            return
        if accepted:
            self._on_accepted(accepted)
