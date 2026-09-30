# -*- coding: utf-8 -*-
"""
Server Client - 与后端 AI 服务通信

负责把消息转发到服务端的 /on-message；服务端只确认收到，AI 回复由服务端异步回调 base 发送。
连上微信时从服务端的 /listen/list 取监听列表。
使用全局 httpx.Client 复用 HTTP 连接，线程安全的熔断器。
"""

import logging
import threading
import time
from typing import Optional

import httpx

from true_love_common.chat_msg import ChatMsg
from true_love_common.hosts import bot_hosts
from true_love_common.http.client import post, post_json
from true_love_base.configuration import Config
from true_love_base.models.api import ChatRequest, ChatResponse

config = Config()
LOG = logging.getLogger("ServerClient")

# 这台机器的机器人对应的 server，地址登记在 true_love_common.hosts
SERVER_HOST = bot_hosts(config.machine_name).server
CHAT_ENDPOINT = f"{SERVER_HOST}/on-message"

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
    在重置超时后自动尝试恢复。
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

    def record_failure(self) -> None:
        """记录一次失败"""
        with self._lock:
            self._fail_count += 1
            self._last_fail_time = time.time()
            LOG.warning(f"Circuit breaker: failure recorded, count={self._fail_count}")

    def record_success(self) -> None:
        """记录一次成功，重置失败计数"""
        with self._lock:
            if self._fail_count > 0:
                LOG.info("Circuit breaker: success recorded, resetting")
            self._fail_count = 0
            self._last_fail_time = 0.0

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

# ==================== API 函数 ====================

def get_chat(msg: ChatMsg) -> str:
    """
    转发消息到服务端，AI 回复由服务端异步回调发送

    Args:
        msg: 消息对象

    Returns:
        服务端接收成功返回空串；失败时返回给用户的提示
    """
    # 检查熔断器
    if _circuit_breaker.is_open():
        LOG.warning("Circuit breaker is open, request rejected")
        return _get_error_message()

    try:
        # 构建请求
        request = ChatRequest(token=config.http_token, message=msg)
        payload = request.to_json()

        # 使用 HTTP client 发起请求（连接复用）
        client = _get_client()
        response = post(
            CHAT_ENDPOINT,
            data=payload,
            timeout=(2, 10),
            client=client,
        )

        # 检查 HTTP 状态
        response.raise_for_status()

        # 解析响应
        resp_data = response.data if isinstance(response.data, dict) else {}
        chat_response = ChatResponse.from_dict(resp_data)

        if chat_response.is_success:
            _circuit_breaker.record_success()
            return ""
        else:
            LOG.error("Server /on-message returned business error: %s", resp_data)
            return _get_error_message()

    except Exception as e:
        LOG.error("Server /on-message failed: %s", e)
        _circuit_breaker.record_failure()
        return _get_error_message()


def _get_error_message() -> str:
    """获取错误提示消息"""
    if _circuit_breaker.fail_count < _circuit_breaker.threshold:
        return "啊哦~消息没送到服务端，稍后再试试捏~"
    return "啊哦~, 服务正在重新调整，请稍后重试再试"


# ==================== 监听列表 ====================

LISTEN_LIST_ENDPOINT = f"{SERVER_HOST}/listen/list"

# 取监听列表的退避重试：机器重启后 base 往往比 Docker 里的 server 先起来
LISTEN_FETCH_DEADLINE = 300  # 秒，过了就放弃，由 server 启动后补监听
LISTEN_FETCH_FIRST_DELAY = 2
LISTEN_FETCH_MAX_DELAY = 60


def _get_listen_chats() -> list[str]:
    """向 server 取一次监听列表，失败时抛异常"""
    response = post_json(LISTEN_LIST_ENDPOINT, {"token": config.http_token}, timeout=(2, 10), client=_get_client())
    response.raise_for_status()
    resp_data = response.data if isinstance(response.data, dict) else {}
    if resp_data.get("code") != 0:
        raise RuntimeError(f"server returned {resp_data}")
    chats = (resp_data.get("data") or {}).get("chats")
    if not isinstance(chats, list):
        raise RuntimeError(f"server returned no chat list: {resp_data}")
    return [str(chat) for chat in chats]


def fetch_listen_chats(stop_event: threading.Event) -> Optional[list[str]]:
    """
    向 server 取监听列表，失败时退避重试

    Returns:
        监听列表；到截止时间还没取到或收到退出信号时返回 None
    """
    deadline = time.monotonic() + LISTEN_FETCH_DEADLINE
    delay = LISTEN_FETCH_FIRST_DELAY
    attempt = 0
    while True:
        attempt += 1
        try:
            return _get_listen_chats()
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
