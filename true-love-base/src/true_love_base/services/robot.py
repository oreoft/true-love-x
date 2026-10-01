# -*- coding: utf-8 -*-
"""
Robot - 消息处理机器人

负责消息监听、处理和转发。
通过 WxAutoClient 操作微信，不直接调用 SDK。
支持异步消息处理，按 chat_id 分组保证同一聊天的消息顺序。
"""

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from true_love_common.chat_msg import ChatMsg
from true_love_base.core import WxAutoClient
from true_love_base.services import server_client
from true_love_base.services.friend_acceptor import FriendAcceptor
from true_love_base.services.private_poller import PrivatePoller


class Robot:
    """
    消息处理机器人
    
    负责:
    - 消息监听和分发
    - 异步消息处理（按 chat_id 分组保证顺序）
    - 消息发送
    """

    # 线程池配置
    MAX_WORKERS = 10

    def __init__(self, client: WxAutoClient, master: str = "",
                 master_of: Optional[Callable[[str], str]] = None) -> None:
        """
        初始化机器人
        
        Args:
            client: 微信客户端实例
            master: 管理员昵称，没有时为空串
            master_of: 按当前登录的号查管理员；给了就忽略 master（号是连上微信后才读到的）
        """
        self.client = client
        self._master = master
        self._master_of = master_of
        self.LOG = logging.getLogger("Robot")

        # 消息处理线程池
        self._executor = ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS,
            thread_name_prefix="MsgHandler"
        )
        self._submission_lock = threading.Lock()
        self._accepting_messages = True

        # 每个 chat_id 一个锁，保证同一聊天内消息顺序
        self._chat_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

        # 功能开关随监听列表从 server 取，后台改了会推过来：
        # 没开子窗口的私聊靠主窗口红点轮询来收；自动通过好友申请
        self.private_poller = PrivatePoller(client, self.on_message)
        self.friend_acceptor = FriendAcceptor(client, self._announce_new_friends)

        self.LOG.info(f"Robot initialized, max_workers: {self.MAX_WORKERS}")

    @property
    def master(self) -> str:
        """当前登录的号的管理员昵称，没有时为空串"""
        if self._master_of is not None:
            return self._master_of(self.client.bot_id)
        return self._master

    def forward_msg(self, msg: ChatMsg) -> str:
        """
        转发消息到服务端处理
        
        Args:
            msg: 消息对象
            
        Returns:
            server 接收成功返回空串；失败时返回给用户的提示
        """
        return server_client.get_chat(msg)

    def on_message(self, msg: ChatMsg, chat_name: str) -> None:
        """
        消息回调处理 - 提交到线程池异步处理
        
        Args:
            msg: 收到的消息
            chat_name: 聊天对象名称
        """
        try:
            with self._submission_lock:
                if not self._accepting_messages:
                    self.LOG.debug(
                        "Discarding message during shutdown: chat=%s msg_id=%s",
                        chat_name, getattr(msg, "msg_hash", "") or getattr(msg, "msg_id", ""),
                    )
                    return
                self.LOG.info(f"Received message from [{chat_name}], submitting to thread pool")
                self._executor.submit(self._process_message, msg, chat_name)
        except Exception as e:
            self.LOG.error(f"Error submitting message to thread pool: {e}")

    def _process_message(self, msg: ChatMsg, chat_name: str) -> None:
        """
        实际处理消息 - 在线程池中执行
        
        同一 chat_name 的消息会串行处理，保证顺序。
        不同 chat_name 的消息可以并行处理。
        
        Args:
            msg: 收到的消息
            chat_name: 聊天对象名称
        """
        # 获取该聊天的锁，保证同一 chat_name 的消息顺序
        with self._chat_locks[chat_name]:
            try:
                # 以 msg_hash 作为 trace_id 起点（线程内设置，不影响其他线程）
                from true_love_common.observability.trace import set_trace_id
                set_trace_id(msg.msg_hash or msg.msg_id or '-')

                self.LOG.info(f"Processing message from [{chat_name}]: {msg}")

                # 所有消息转发给 server，由 server 负责路由（存储 + 决定是否触发 AI）
                error_reply = self.forward_msg(msg)

                # server 没接住就不会有 AI 回复，需要回复的消息由 base 直接提示用户
                if error_reply and (msg.is_at_me or not msg.is_group):
                    self.send_text_msg(error_reply, chat_name, msg.sender_id if msg.is_group else None)

            except Exception as e:
                self.LOG.error(f"Error processing message from [{chat_name}]: {e}")

    # 监听添加重试配置
    LISTEN_ADD_RETRY_COUNT = 3
    LISTEN_ADD_RETRY_DELAY = 1.0  # 秒

    def add_listen_chat(
        self, chat_name: str, *, stop_event: Optional[threading.Event] = None
    ) -> bool:
        """
        添加监听的聊天对象（仅操作 SDK，不写入文件）
        
        Note: 监听列表由 Server 端的 ListenManager 保存
        
        Args:
            chat_name: 聊天对象名称（好友昵称或群名）
            stop_event: 启动取消信号，停止后续尝试和重试等待
            
        Returns:
            是否添加成功
        """
        import time

        max_attempts = self.LISTEN_ADD_RETRY_COUNT
        last_error = None

        for attempt in range(1, max_attempts + 1):
            if stop_event is not None and stop_event.is_set():
                self.LOG.debug(
                    "Listener registration cancelled by shutdown before attempt: chat=%s attempt=%s/%s",
                    chat_name, attempt, max_attempts,
                )
                return False
            try:
                success = self.client.add_message_listener(chat_name, self.on_message)
                if success:
                    self.LOG.info(f"Started listening to [{chat_name}] (attempt {attempt})")
                    return True
                else:
                    self.LOG.warning(f"Failed to add listener for [{chat_name}] (attempt {attempt}/{max_attempts})")
            except Exception as e:
                last_error = e
                self.LOG.warning(
                    "Exception adding listener for [%s] (attempt %s/%s)",
                    chat_name, attempt, max_attempts, exc_info=True,
                )

            # 如果不是最后一次尝试，等待后重试
            if attempt < max_attempts:
                if stop_event is None:
                    time.sleep(self.LISTEN_ADD_RETRY_DELAY)
                elif stop_event.wait(self.LISTEN_ADD_RETRY_DELAY):
                    self.LOG.debug(
                        "Listener retry cancelled by shutdown while waiting: chat=%s completed_attempts=%s/%s",
                        chat_name, attempt, max_attempts,
                    )
                    return False

        self.LOG.error(
            f"Failed to add listener for [{chat_name}] after {max_attempts} attempts. Last error: {last_error}")
        return False

    def load_listen_chats(
        self, *, stop_event: threading.Event
    ) -> dict:
        """
        向 server 取监听设置，开始监听并按开关启停各项功能；取不到时一个都不监听，开关保持原样

        Returns:
            包含成功和失败列表的字典:
            - success: 成功监听的聊天列表
            - failed: 监听失败的聊天列表
            - unavailable: 没从 server 取到监听列表时为 True
            - switches: 各项功能开关现在的状态
        """
        setup = server_client.fetch_listen_chats(stop_event)
        if setup is None:
            return {"success": [], "failed": [], "unavailable": True, "switches": self.switches()}
        chats = setup.chats
        self.apply_switches({name: setup.switches.get(name, False) for name in self._switchable()})
        self.LOG.info(f"Loading {len(chats)} listen chats from server, switches: {self.switches()}")

        success = []
        failed = []

        for chat_name in chats:
            if stop_event.is_set():
                self.LOG.info(
                    "Listener loading cancelled before [%s]: success=%s failed=%s remaining=%s",
                    chat_name, len(success), len(failed), len(chats) - len(success) - len(failed),
                )
                break
            if self.add_listen_chat(chat_name, stop_event=stop_event):
                success.append(chat_name)
            elif stop_event.is_set():
                self.LOG.info(
                    "Listener loading cancelled while registering [%s]: success=%s failed=%s remaining=%s",
                    chat_name, len(success), len(failed), len(chats) - len(success) - len(failed),
                )
                break
            else:
                failed.append(chat_name)

        return {"success": success, "failed": failed, "unavailable": False, "switches": self.switches()}

    # ==================== 功能开关 ====================

    def _switchable(self) -> dict:
        return {"private_poll": self.private_poller, "auto_accept_friends": self.friend_acceptor}

    def switches(self) -> dict[str, bool]:
        """各项功能开关现在的状态"""
        return {name: feature.enabled for name, feature in self._switchable().items()}

    def apply_switches(self, switches: dict[str, bool]) -> dict[str, bool]:
        """
        打开或关闭功能，不认识的开关忽略

        Returns:
            各项功能开关现在的状态
        """
        features = self._switchable()
        for name, enabled in switches.items():
            if name in features:
                features[name].set_enabled(bool(enabled))
        return self.switches()

    def _announce_new_friends(self, requests: list[str]) -> None:
        """自动通过了好友申请后告诉管理员"""
        if not self.master:
            return
        lines = "\n".join(f"  {i + 1}. {text}" for i, text in enumerate(requests))
        if not self.send_text_msg(f"已自动通过 {len(requests)} 个好友申请：\n{lines}", self.master):
            self.LOG.warning("Friend acceptance notice was not delivered to [%s]", self.master)

    def cleanup(self) -> None:
        """
        清理资源
        
        关闭线程池，等待所有任务完成。
        """
        self.private_poller.stop()
        self.friend_acceptor.stop()
        # SDK callbacks already in flight may arrive after StopListening returns.
        with self._submission_lock:
            self._accepting_messages = False
        self.LOG.info("Robot cleanup: shutting down thread pool...")
        self._executor.shutdown(wait=True, cancel_futures=False)
        self.LOG.info("Robot cleanup: thread pool shutdown complete")

    # ==================== 消息发送 ====================

    def send_text_msg(self, msg: str, receiver: str, at_user: Optional[str] = None) -> bool:
        """
        发送文本消息
        
        Args:
            msg: 消息内容
            receiver: 接收者
            at_user: 要@的用户（可选）
            
        Returns:
            是否发送成功
        """
        if not msg or not msg.strip():
            return False

        at_list = [at_user] if at_user else None
        self.LOG.info(f"Sending to [{receiver}]: {msg[:50]}...")
        return self.client.send_text(receiver, msg, at_list)

    def send_file_msg(self, path: str, receiver: str) -> bool:
        """
        发送文件
        
        Args:
            path: 文件路径
            receiver: 接收者
            
        Returns:
            是否发送成功
        """
        self.LOG.info(f"Sending file to [{receiver}]: {path}")
        return self.client.send_file(receiver, path)
