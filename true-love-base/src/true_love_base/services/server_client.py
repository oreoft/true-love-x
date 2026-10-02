# -*- coding: utf-8 -*-
"""
Server Client - 与后端 AI 服务通信

负责把消息转发到服务端的 /base/on-message；服务端只确认收到，AI 回复由服务端异步回调 base 发送。
连上微信时从服务端的 /base/listen/list 取监听列表和各项设置。
每次请求都带上这个 base 的机器人信息（bot_id、回调地址、昵称），server 据此登记和回调。
使用全局 httpx.Client 复用 HTTP 连接，线程安全的熔断器；没送到的消息放进本地队列，后台退避补发。
"""

import logging
import threading
import time
from collections import deque
from typing import Callable, NamedTuple, Optional

import httpx

from true_love_common.bot import BotInfo
from true_love_common.chat_msg import ChatMsg
from true_love_common.hosts import SERVER_HOST
from true_love_common.http.client import post, post_json
from true_love_base.configuration import Config
from true_love_base.models.api import ChatRequest, ChatResponse

config = Config()
LOG = logging.getLogger("ServerClient")

# 所有 base 共用一个 server，地址写在 true_love_common.hosts
CHAT_ENDPOINT = f"{SERVER_HOST}/base/on-message"

# 当前登录的号和昵称，由 main 在创建微信客户端后接上；号是连上微信时从本机读出来的
_bot_id: Callable[[], str] = lambda: ""
_self_name: Callable[[], str] = lambda: ""


def use_identity(bot_id: Callable[[], str], self_name: Callable[[], str]) -> None:
    """报给 server 的号和昵称从哪里取"""
    global _bot_id, _self_name
    _bot_id, _self_name = bot_id, self_name


def bot_info() -> BotInfo:
    """这个 base 的机器人信息，每次调 server 都带上"""
    return BotInfo(bot_id=_bot_id(), platform="wechat", callback=config.callback, name=_self_name())

# ==================== HTTP Client 连接复用 ====================

_client = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    """
    获取全局 HTTP Client（线程安全）
    
    复用 TCP 连接，减少握手开销。
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(headers={"Content-Type": "application/json"})
                LOG.info("HTTP client created for connection reuse")
    return _client


# ==================== 线程安全的熔断器 ====================

class CircuitBreaker:
    """
    线程安全的熔断器
    
    当连续失败次数超过阈值时，熔断器打开，
    在重置超时后自动尝试恢复。打开和恢复各报一次，中途半开再失败不重复报。
    """

    def __init__(self, threshold: int = 3, reset_timeout: int = 60):
        """
        初始化熔断器
        
        Args:
            threshold: 失败阈值，超过后熔断器打开
            reset_timeout: 重置超时时间（秒）
        """
        self._lock = threading.Lock()
        self._fail_count = 0
        self._last_fail_time = 0.0
        self._threshold = threshold
        self._reset_timeout = reset_timeout
        # 打开后到下一次成功之前为 True
        self._tripped = False
        self._tripped_at = 0.0
        self._on_open: Optional[Callable[[], None]] = None

    def on_open(self, callback: Optional[Callable[[], None]]) -> None:
        """熔断器打开时调用一次"""
        self._on_open = callback

    def record_failure(self) -> None:
        """记录一次失败"""
        with self._lock:
            self._fail_count += 1
            self._last_fail_time = time.time()
            opened = self._fail_count >= self._threshold and not self._tripped
            if opened:
                self._tripped = True
                self._tripped_at = self._last_fail_time
        if not opened:
            return
        LOG.error("Circuit breaker opened after %s consecutive failures; messages are queued for resending",
                  self._threshold)
        callback = self._on_open
        if callback is not None:
            try:
                callback()
            except Exception:
                LOG.exception("Circuit breaker open notification failed")

    def record_success(self) -> None:
        """记录一次成功，重置失败计数"""
        with self._lock:
            recovered = self._tripped
            down_for = time.time() - self._tripped_at
            self._fail_count = 0
            self._last_fail_time = 0.0
            self._tripped = False
        if recovered:
            LOG.error("Circuit breaker recovered: server is reachable again after %.0fs", down_for)

    def is_open(self) -> bool:
        """
        检查熔断器是否打开
        
        Returns:
            True 表示熔断器打开（应该拒绝请求）
        """
        with self._lock:
            if self._fail_count < self._threshold:
                return False

            # 检查是否超过重置时间
            if time.time() - self._last_fail_time >= self._reset_timeout:
                LOG.info("Circuit breaker: reset timeout reached, allowing retry")
                self._fail_count = 0
                self._last_fail_time = 0.0
                return False

            return True

    @property
    def threshold(self) -> int:
        """失败阈值"""
        return self._threshold

    @property
    def fail_count(self) -> int:
        """获取当前失败次数"""
        with self._lock:
            return self._fail_count


# 全局熔断器实例
_circuit_breaker = CircuitBreaker(threshold=3, reset_timeout=60)


def on_server_down(callback: Optional[Callable[[], None]]) -> None:
    """连续转发失败、熔断器打开时调用一次，用来通知管理员"""
    _circuit_breaker.on_open(callback)


# ==================== API 函数 ====================

class UnsendableMessage(Exception):
    """消息本身转不成请求，重发也一样，不算 server 连不上"""


def _post_chat(msg: ChatMsg, *, archive_only: bool = False) -> bool:
    """把消息交给 server，返回 server 是否收下；失败时记日志、记熔断。消息转不成请求时抛 UnsendableMessage"""
    try:
        body = ChatRequest(token=config.http_token, bot=bot_info(), message=msg,
                           archive_only=archive_only).to_json()
    except Exception as e:
        raise UnsendableMessage(f"{type(e).__name__}: {e}") from e
    try:
        # 使用 HTTP client 发起请求（连接复用）
        response = post(
            CHAT_ENDPOINT,
            data=body,
            timeout=(2, 10),
            client=_get_client(),
        )
        response.raise_for_status()

        resp_data = response.data if isinstance(response.data, dict) else {}
        chat_response = ChatResponse.from_dict(resp_data)
    except Exception as e:
        LOG.warning("Server /base/on-message failed: chat=%s msg_hash=%s: %s: %s",
                    msg.chat_name, msg.msg_hash, type(e).__name__, e)
        _circuit_breaker.record_failure()
        return False

    if not chat_response.is_success:
        LOG.error("Server /base/on-message returned business error: chat=%s msg_hash=%s: %s",
                  msg.chat_name, msg.msg_hash, resp_data)
        return False
    _circuit_breaker.record_success()
    return True


def get_chat(msg: ChatMsg) -> str:
    """
    转发消息到服务端，AI 回复由服务端异步回调发送

    Args:
        msg: 消息对象

    Returns:
        服务端接收成功返回空串；失败时返回给用户的提示，消息由调用方交给 retry_later。
        消息转不成请求时打 error 后丢弃，也返回空串：补发和熔断都帮不上忙
    """
    if _circuit_breaker.is_open():
        LOG.warning("Circuit breaker is open, not forwarding: chat=%s msg_hash=%s", msg.chat_name, msg.msg_hash)
        return _get_error_message()
    try:
        if _post_chat(msg):
            return ""
    except UnsendableMessage as e:
        LOG.error("Dropped a message that cannot be sent to server: chat=%s msg_hash=%s: %s",
                  msg.chat_name, msg.msg_hash, e)
        return ""
    return _get_error_message()


# 消息没交给 server 时回给发消息的人：偶尔失败一次，和连续失败到熔断
SEND_FAILED_REPLY = "啊哦~消息没送到服务端，稍后再试试捏~"
SERVER_DOWN_REPLY = "啊哦~, 服务正在重新调整，请稍后重试再试"


def _get_error_message() -> str:
    """获取错误提示消息"""
    if _circuit_breaker.fail_count < _circuit_breaker.threshold:
        return SEND_FAILED_REPLY
    return SERVER_DOWN_REPLY


# ==================== 补发队列 ====================

RETRY_MAX_COUNT = 200
RETRY_MAX_AGE = 600  # 秒，过了就不补发
RETRY_FIRST_DELAY = 5
RETRY_MAX_DELAY = 60


class _Pending(NamedTuple):
    msg: ChatMsg
    archive_only: bool
    queued_at: float


class RetryQueue:
    """
    转发失败的消息先存在本地，后台线程按退避补发

    按先后顺序补发，前面的发不出去就等下一轮；超出条数或放太久的丢弃并打 warning。
    """

    def __init__(self, send: Callable[..., bool], *, max_count: int = RETRY_MAX_COUNT,
                 max_age: float = RETRY_MAX_AGE, first_delay: float = RETRY_FIRST_DELAY,
                 max_delay: float = RETRY_MAX_DELAY, clock: Callable[[], float] = time.monotonic) -> None:
        self._send = send
        self._max_count = max_count
        self._max_age = max_age
        self._first_delay = first_delay
        self._max_delay = max_delay
        self._clock = clock
        self._items: deque[_Pending] = deque()
        self._lock = threading.Lock()
        # 同一时间只有一个线程在补发，保证顺序
        self._flush_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def put(self, msg: ChatMsg, *, archive_only: bool = False) -> None:
        with self._lock:
            self._items.append(_Pending(msg, archive_only, self._clock()))
            while len(self._items) > self._max_count:
                dropped = self._items.popleft()
                LOG.warning("Resend queue is full (%s), dropped: chat=%s msg_hash=%s",
                            self._max_count, dropped.msg.chat_name, dropped.msg.msg_hash)
            size = len(self._items)
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._run, name="ServerResend", daemon=True)
                self._thread.start()
        LOG.warning("Queued for resending: chat=%s msg_hash=%s pending=%s", msg.chat_name, msg.msg_hash, size)

    def flush(self) -> bool:
        """按顺序补发一轮，全部发完返回 True，遇到发不出去的就停下返回 False"""
        with self._flush_lock:
            while True:
                with self._lock:
                    self._drop_expired()
                    if not self._items:
                        return True
                    item = self._items[0]
                try:
                    if not self._send(item.msg, archive_only=item.archive_only):
                        return False
                except UnsendableMessage as e:
                    # 重发也一样，留着会挡住后面的消息
                    LOG.error("Dropped a queued message that cannot be sent to server: chat=%s msg_hash=%s: %s",
                              item.msg.chat_name, item.msg.msg_hash, e)
                else:
                    LOG.info("Resent to server: chat=%s msg_hash=%s after %.0fs",
                             item.msg.chat_name, item.msg.msg_hash, self._clock() - item.queued_at)
                with self._lock:
                    if self._items and self._items[0] is item:
                        self._items.popleft()

    def stop(self) -> None:
        """base 关闭时停止补发，还没发出去的打 warning"""
        self._stop.set()
        with self._lock:
            left, self._items = list(self._items), deque()
        for item in left:
            LOG.warning("Shutting down before resending: chat=%s msg_hash=%s",
                        item.msg.chat_name, item.msg.msg_hash)

    def _drop_expired(self) -> None:
        now = self._clock()
        while self._items and now - self._items[0].queued_at > self._max_age:
            dropped = self._items.popleft()
            LOG.warning("Gave up resending after %.0fs: chat=%s msg_hash=%s",
                        now - dropped.queued_at, dropped.msg.chat_name, dropped.msg.msg_hash)

    def _run(self) -> None:
        delay = self._first_delay
        while not self._stop.wait(delay):
            try:
                done = self.flush()
            except Exception:
                LOG.exception("Resending to server failed")
                done = False
            delay = self._first_delay if done else min(delay * 2, self._max_delay)


_retry_queue = RetryQueue(_post_chat)


def retry_later(msg: ChatMsg, *, archive_only: bool = False) -> None:
    """
    没交给 server 的消息放进补发队列

    Args:
        archive_only: 已经提示过用户重发时为 True，补发只为存档
    """
    _retry_queue.put(msg, archive_only=archive_only)


def stop_retrying() -> None:
    """base 关闭时调用，没补发出去的消息打 warning"""
    _retry_queue.stop()


# ==================== 监听列表 ====================

LISTEN_LIST_ENDPOINT = f"{SERVER_HOST}/base/listen/list"

# 取监听列表的退避重试：机器重启后 base 往往比 Docker 里的 server 先起来
LISTEN_FETCH_DEADLINE = 300  # 秒，过了就放弃，由 server 启动后补监听
LISTEN_FETCH_FIRST_DELAY = 2
LISTEN_FETCH_MAX_DELAY = 60


class ListenSetup(NamedTuple):
    """server 上这个机器人的监听设置"""
    chats: list[str]  # 开子窗口监听的群和好友
    settings: dict  # 其余设置：私聊轮询 private_poll、自动通过好友申请 auto_accept_friends、群回复方式 group_reply


def _get_listen_setup() -> ListenSetup:
    """向 server 取一次监听设置，失败时抛异常"""
    payload = {"token": config.http_token, "bot": bot_info().to_dict()}
    response = post_json(LISTEN_LIST_ENDPOINT, payload, timeout=(2, 10), client=_get_client())
    response.raise_for_status()
    resp_data = response.data if isinstance(response.data, dict) else {}
    if resp_data.get("code") != 0:
        raise RuntimeError(f"server returned {resp_data}")
    data = resp_data.get("data") or {}
    chats = data.get("chats")
    if not isinstance(chats, list):
        raise RuntimeError(f"server returned no chat list: {resp_data}")
    # 旧 server 没有的设置由 base 用默认值
    settings = {key: value for key, value in data.items() if key != "chats"}
    return ListenSetup([str(chat) for chat in chats], settings)


def fetch_listen_chats(stop_event: threading.Event) -> Optional[ListenSetup]:
    """
    向 server 取监听设置，失败时退避重试

    Returns:
        监听设置；到截止时间还没取到或收到退出信号时返回 None
    """
    deadline = time.monotonic() + LISTEN_FETCH_DEADLINE
    delay = LISTEN_FETCH_FIRST_DELAY
    attempt = 0
    while True:
        attempt += 1
        try:
            return _get_listen_setup()
        except Exception as e:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOG.error("Gave up fetching listen chats from server after %s attempts: %s", attempt, e)
                return None
            wait = min(delay, remaining)
            LOG.warning("Failed to fetch listen chats from server (attempt %s), retrying in %.0fs: %s",
                        attempt, wait, e)
            if stop_event.wait(wait):
                return None
            delay = min(delay * 2, LISTEN_FETCH_MAX_DELAY)
