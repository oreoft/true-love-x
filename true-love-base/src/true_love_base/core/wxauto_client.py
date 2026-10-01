# -*- coding: utf-8 -*-
"""
WxAuto WxAutoClient - wxautox4 SDK 封装客户端

封装 wxautox4 的具体调用。
"""

import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from threading import Lock, local
from typing import Callable, Optional

# This is a special import, please do not modify
from true_love_base.wxautox4x.wxautox4x import WeChat
from wxautox4.param import WxParam
from wxautox4.uia.uiautomation import InitializeUIAutomationInCurrentThread
from wxautox4.utils.lock import ui_transaction

from true_love_common.chat_msg import ChatMsg
from true_love_base.models.message_converter import convert_message
from true_love_base.utils.path_resolver import get_wx_imgs_dir

LOG = logging.getLogger("WxAutoClient")

# 用消息哈希辅助判断新消息；每次启动重新测量头像到消息的 X 偏移量，不沿用上次的值
WxParam.MESSAGE_HASH = True
WxParam.FORCE_MESSAGE_XBIAS = True
WxParam.CHAT_WINDOW_SIZE = (8000, 6000)
# 下载的文件默认存到 base 工作目录下的 wx_imgs，通过 /media 开放给别的服务
_wx_imgs_dir = get_wx_imgs_dir()
if _wx_imgs_dir:
    WxParam.DEFAULT_SAVE_PATH = _wx_imgs_dir
    LOG.info("Set WxParam.DEFAULT_SAVE_PATH to: %s", _wx_imgs_dir)

MessageCallback = Callable[[ChatMsg, str], None]

# SendFiles / SendAudio 偶尔报失败但文件其实已经发出去了（m8s 上约一成）。
# 报失败后最多等这么久，看监听有没有收到自己刚发出的这条消息，收到就按成功算
SEND_CONFIRM_SECONDS = 10
# 自己发出的文件在监听里的消息类型
_SELF_MEDIA_TYPES = {"image", "file", "video", "voice"}
# 拍一拍和引用要拿着原消息在聊天窗口里的那个对象去点右键菜单，所以收到的消息按 id 留一段时间
REPLY_KEEP_SECONDS = 600
REPLY_KEEP_COUNT = 500
TICKLE_SETTLE_SECONDS = 1.5
# 不转给 server 的消息：微信团队、系统提示、自己发的
_IGNORED_ATTRS = {"weixin", "system", "self"}
# 消息转换或处理出错时回给对方的提示
BROKEN_MESSAGE_REPLY = "啊咧？消息好像坏掉了，麻烦再发一次吧~"

# SDK 操作界面的方法（发消息、加监听、切换会话）都在 ui_transaction 里执行，它可重入，跨线程也跨进程；
# GetNextNewMessage 和会话右键菜单没有加锁，要自己包一层，免得轮询点开会话时正好有人在主窗口里发消息
# 会话右键菜单里开关免打扰的选项
_MUTE_OPTION = "消息免打扰"
# 会话右键菜单里把聊天弹成独立窗口的选项，和弹出后等窗口出现的秒数
_POP_OUT_OPTION = "独立窗口显示"
_POP_OUT_WAIT = 3.0

# 在主线程以外读会话列表要先在这个线程里初始化 UIA；不初始化时 GetNextNewMessage 不报错，只是永远拿不到消息
_uia_thread = local()


def _init_uia_in_thread() -> None:
    if not getattr(_uia_thread, "ready", False):
        InitializeUIAutomationInCurrentThread()
        _uia_thread.ready = True


def _window_titled(hwnd: int, title: str) -> bool:
    """这个窗口还在，标题也还是 title"""
    import win32gui

    return bool(win32gui.IsWindow(hwnd)) and win32gui.GetWindowText(hwnd) == title


class WxAutoClient():
    """
    wxautox4 客户端适配器 封装 wxautox4 的所有操作。
    """

    def __init__(self, bot_id: str = "", account_of: Optional[Callable[[], str]] = None):
        """
        创建客户端；不连接微信，微信可用时由 connect() 建立连接

        Args:
            bot_id: 这个机器人的标识（微信是 wxid），随每条消息带给下游
            account_of: 读出当前登录的微信号；给了就在每次连接时读一次，用它作为 bot_id
        """
        self._wx = None
        self._running = True
        self._bot_id = bot_id
        self._account_of = account_of
        # 当前登录账号的昵称，每次连接时从微信读取
        self._self_name: str = ""
        # 当前状态（在线或离线）开始的时间
        self._state_since = datetime.now()
        # 最近一次连接失败的原因，同一个原因只报一次
        self._connect_error: Optional[str] = None
        # Coordinate listener registration with shutdown, independently of SDK UI locks.
        self._lifecycle_lock = Lock()
        # 监听里看到的自己发出的文件：{聊天对象: [(时间, 类型, 内容)]}，只保留最近几条
        self._self_media: dict[str, list[tuple[float, str, str]]] = {}
        self._self_media_lock = Lock()
        # 能拍一拍、引用回复的消息：{消息 id: (收到的时间, 聊天对象, SDK 消息对象)}，按收到的先后排
        self._replyable: OrderedDict[str, tuple[float, str, object]] = OrderedDict()
        self._replyable_lock = Lock()
        # 每个监听注册时弹出的聊天窗口句柄：{聊天对象: HWND}，查监听状态时只看这个窗口还在不在
        self._listen_windows: dict[str, int] = {}

    def connect(self) -> bool:
        """连接已登录的微信主窗口；微信没开或没登录时返回 False，由调用方稍后重试"""
        try:
            wx = WeChat(version='WeChat')
            # 主窗口还在但已经掉线（比如断网）时先不接管，等它恢复在线
            if not wx.IsOnline():
                raise RuntimeError("WeChat main window is open but not online")
            if self._account_of is not None:
                self._bot_id = self._account_of()
        except Exception as e:
            # 离线期间每隔几秒就会重试一次
            level = logging.DEBUG if str(e) == self._connect_error else logging.WARNING
            self._connect_error = str(e)
            LOG.log(level, "WeChat is not available: %s", e)
            return False
        self._wx = wx
        self._connect_error = None
        self._self_name = str(getattr(wx, 'nickname', None) or "")
        self._state_since = datetime.now()
        LOG.info("WxAutoClient connected, self: %s, bot_id: %s", self.get_self_name(), self._bot_id)
        return True

    def disconnect(self) -> None:
        """微信掉线后丢弃当前实例；监听在下一次 connect() 之后重新注册"""
        # Wait for an in-flight registration, so it cannot land on the instance being dropped.
        with self._lifecycle_lock:
            wx, self._wx = self._wx, None
        if wx is None:
            return
        self._listen_windows.clear()
        self._self_name = ""
        self._state_since = datetime.now()
        # Do not hold the lifecycle lock while the SDK stops its listener threads.
        try:
            wx.StopListening(remove=False)
        except Exception:
            LOG.warning("Failed to stop listening on the dropped WeChat instance", exc_info=True)
        LOG.info("WxAutoClient disconnected")

    def check_online(self) -> bool:
        """向微信确认登录态；被挤下线、退出登录、窗口已不存在都算掉线"""
        wx = self._wx
        if wx is None:
            return False
        try:
            return bool(wx.IsOnline())
        except Exception as e:
            LOG.warning("WeChat online check failed: %s", e)
            return False

    def is_connected(self) -> bool:
        """微信当前是否可用：已连接，且 base 没有在关闭"""
        return self._running and self._wx is not None

    def status(self) -> dict:
        """微信连接状态，供 /status 接口返回"""
        online = self.is_connected()
        return {
            "wx_online": online,
            "bot_id": self._bot_id,
            "self_name": self.get_self_name() if online else None,
            "since": self._state_since.isoformat(timespec="seconds"),
        }

    @property
    def wx(self):
        """获取底层 WeChat 实例"""
        if self._wx is None:
            raise RuntimeError("WeChat client not initialized")
        return self._wx

    # ==================== 通用方法 ====================

    def _check_response(self, result, action: str, target: str) -> bool:
        """
        检查 WxResponse 结果
        
        Args:
            result: WxResponse 类型的返回值
            action: 操作名称，用于日志
            target: 操作目标，用于日志
            
        Returns:
            bool: 操作是否成功
        """
        if result:
            LOG.info("[%s] [%s] succeeded", action, target)
            return True
        else:
            # WxResponse 失败时，通过 result['message'] 获取错误信息
            error_msg = result.get('message', 'Unknown error') if isinstance(result, dict) else str(result)
            LOG.error("[%s] [%s] failed: %s", action, target, error_msg)
            return False

    # ==================== 账号信息 ====================

    @property
    def bot_id(self) -> str:
        """当前登录的号（微信是 wxid），连上微信之前可能为空"""
        return self._bot_id

    def get_self_name(self) -> str:
        """获取当前登录账号昵称"""
        return self._self_name or "Unknown"

    @staticmethod
    def _dump_obj_attrs(obj) -> str:
        """
        获取对象的所有公开属性（非方法、非私有），返回格式化的 JSON 字符串
        
        Args:
            obj: 要打印的对象
            
        Returns:
            格式化的属性字符串
        """
        result = {}
        for attr in dir(obj):
            if attr.startswith('_'):
                continue
            try:
                value = getattr(obj, attr)
                if not callable(value):
                    # 尝试转为可序列化的类型
                    try:
                        json.dumps(value, ensure_ascii=False)
                        result[attr] = value
                    except (TypeError, ValueError):
                        result[attr] = repr(value)
            except Exception:
                pass
        return json.dumps(result, ensure_ascii=False, indent=2)

    # ==================== 消息发送 ====================

    def send_text(self, receiver: str, content: str, at_list: Optional[list[str]] = None,
                  reply_msg_id: str = "", reply_style: str = "at") -> bool:
        """
        发送文本消息

        Args:
            reply_msg_id: 这条是在回复哪条群消息
            reply_style: 怎么回复这条群消息：at 直接 @；tickle 先拍一拍再发，拍到了就不再 @，拍不了时照常 @；
                quote 引用原消息，原消息已经不在监听窗口里或引用不了时照常 @ 发送
        """
        if reply_msg_id and reply_style != "at":
            raw_msg = self._replied_message(receiver, reply_msg_id, reply_style)
            if reply_style == "quote" and raw_msg is not None and self._quote(receiver, raw_msg, content):
                return True
            # 拍到了就不再 @；拍不了时照常 @
            if reply_style == "tickle" and raw_msg is not None and self._tickle(receiver, raw_msg):
                at_list = None
        try:
            LOG.debug("SendMsg content: %s...", content[:50])
            sub_window = self.wx.GetSubWindow(receiver)
            result = (
                sub_window.SendMsg(content, at=at_list)
                if sub_window
                else self.wx.SendMsg(content, receiver, at=at_list)
            )
            return self._check_response(result, "SendMsg", receiver)
        except Exception:
            LOG.exception("Failed to send text to [%s]", receiver)
            return False

    def _replied_message(self, receiver: str, msg_id: str, style: str):
        """被回复的那条消息还在监听窗口里时返回它，否则返回 None"""
        with self._replyable_lock:
            entry = self._replyable.get(msg_id)
        if entry is None or entry[1] != receiver or time.monotonic() - entry[0] > REPLY_KEEP_SECONDS:
            LOG.info("[Reply] [%s] message %s is no longer kept; replying without %s", receiver, msg_id, style)
            return None
        raw_msg = entry[2]
        try:
            if raw_msg.exists():
                return raw_msg
        except Exception:
            LOG.warning("[Reply] [%s] checking message %s failed", receiver, msg_id, exc_info=True)
        LOG.info("[Reply] [%s] message %s left the chat window; replying without %s", receiver, msg_id, style)
        return None

    @staticmethod
    def _quote(receiver: str, raw_msg, content: str) -> bool:
        """引用原消息回复；引用不了时返回 False（比如屏幕缩放不是 100% 时右键点不到消息）"""
        try:
            result = raw_msg.quote(content)
        except Exception:
            LOG.warning("[Reply] [%s] quoting failed; falling back to @", receiver, exc_info=True)
            return False
        if not result:
            LOG.warning("[Reply] [%s] quoting failed: %s; falling back to @",
                        receiver, result.get('message') if isinstance(result, dict) else result)
            return False
        LOG.info("[Reply] [%s] replied by quoting", receiver)
        return True

    @staticmethod
    def _tickle(receiver: str, raw_msg) -> bool:
        """拍一拍原消息的发送人；SDK 不返回结果，没报错就算拍到了"""
        try:
            raw_msg.tickle()
        except Exception:
            LOG.warning("[Reply] [%s] tickling failed; falling back to @", receiver, exc_info=True)
            return False
        LOG.info("[Reply] [%s] tickled the sender", receiver)
        # 等“拍了拍”的提示先出来，回复再跟在它后面
        time.sleep(TICKLE_SETTLE_SECONDS)
        return True

    def _keep_for_reply(self, chat_name: str, raw_msg) -> None:
        msg_id = str(getattr(raw_msg, "id", "") or "")
        if not msg_id:
            return
        now = time.monotonic()
        with self._replyable_lock:
            self._replyable[msg_id] = (now, chat_name, raw_msg)
            self._replyable.move_to_end(msg_id)
            while self._replyable and (len(self._replyable) > REPLY_KEEP_COUNT
                                       or now - next(iter(self._replyable.values()))[0] > REPLY_KEEP_SECONDS):
                self._replyable.popitem(last=False)

    _AUDIO_EXTS = (".wav", ".mp3")

    def send_file(self, receiver: str, file_path: str) -> bool:
        """发送文件；音频文件（.wav/.mp3）走原生语音气泡 SendAudio，失败直接返回失败，不降级为普通文件"""
        if file_path.lower().endswith(self._AUDIO_EXTS):
            try:
                return self._send_audio(receiver, file_path)
            except Exception:
                LOG.exception("SendAudio failed for [%s]", receiver)
                return False
        return self._send_file_generic(receiver, file_path)

    def _send_audio(self, receiver: str, file_path: str) -> bool:
        """[Beta] 发送语音条消息，需要 4.1.9+ 客户端"""
        LOG.debug("SendAudio path: %s", file_path)
        sub_window = self.wx.GetSubWindow(receiver)
        started = time.monotonic()
        result = sub_window.SendAudio(file_path) if sub_window else self.wx.SendAudio(file_path, who=receiver)
        if not result and sub_window and self._confirm_sent(receiver, file_path, started):
            return True
        return self._check_response(result, "SendAudio", receiver)

    def _send_file_generic(self, receiver: str, file_path: str) -> bool:
        try:
            LOG.debug("SendFiles path: %s", file_path)
            sub_window = self.wx.GetSubWindow(receiver)
            started = time.monotonic()
            result = sub_window.SendFiles(file_path) if sub_window else self.wx.SendFiles(file_path, receiver)
            if not result and sub_window and self._confirm_sent(receiver, file_path, started):
                return True
            return self._check_response(result, "SendFiles", receiver)
        except Exception:
            LOG.exception("Failed to send file to [%s]", receiver)
            return False

    def _confirm_sent(self, receiver: str, file_path: str, started: float) -> bool:
        """
        SDK 报发送失败后，等监听里出现自己刚发出的这条消息；只有监听中的聊天能这样确认

        文件消息的内容里带文件名，要求文件名对得上；图片、视频、语音的内容里没有文件名，出现就算。
        认领到的回显从记录里删掉，一条回显只能确认一次发送，免得把上一张图的回显认成这一张。
        """
        filename = os.path.basename(file_path)
        deadline = started + SEND_CONFIRM_SECONDS
        while True:
            with self._self_media_lock:
                recent = self._self_media.get(receiver, [])
                claimed = next((entry for entry in recent
                                if entry[0] >= started and (entry[1] != "file" or filename in entry[2])), None)
                if claimed is not None:
                    recent.remove(claimed)
            if claimed is not None:
                LOG.warning("[SendFiles] [%s] reported failure, but [%s] showed up in the chat %.1fs later; "
                            "treating it as sent", receiver, filename, claimed[0] - started)
                return True
            if time.monotonic() >= deadline:
                LOG.warning("[SendFiles] [%s] [%s] did not show up in the chat within %ss",
                            receiver, filename, SEND_CONFIRM_SECONDS)
                return False
            time.sleep(0.5)

    def _remember_self_media(self, chat_name: str, raw_msg) -> None:
        kind = str(getattr(raw_msg, 'type', '') or '')
        if kind not in _SELF_MEDIA_TYPES:
            return
        content = str(getattr(raw_msg, 'content', '') or '')
        with self._self_media_lock:
            recent = self._self_media.setdefault(chat_name, [])
            recent.append((time.monotonic(), kind, content))
            del recent[:-20]

    # ==================== 消息监听 ====================

    def _create_internal_callback(self, chat_name: str, callback: MessageCallback):
        """
        创建内部回调函数
        
        将用户回调包装成 wxauto 需要的内部回调格式，包含消息过滤和格式转换逻辑。
        
        Args:
            chat_name: 聊天对象名称
            callback: 用户回调函数
            
        Returns:
            内部回调函数（签名为 (raw_msg, chat)）
            
        Note:
            wxauto 回调签名是 (msg, chat)，必须接收两个参数
            
            示例 raw_msg 属性：
            私聊
             {
                "attr": "friend", # 获取 attr, 属性system：系统消息 self：自己发送的消息 friend：好友消息 other：其他消息
                "chat_info": {
                    "chat_type": "friend",
                    "chat_name": "纯路人"
                    },
                "content": "hello",
                "control": "<wxautox4.uia.uiautomation.ListItemControl object at 0x0000023CA83F92B0>",
                "hash": "0c6a86758fe737c7d0c3a9fd28474bb0",
                "hash_text": "(56,360)hello",
                "id": "cd886a97fd1c05f6d49f628d6c114d16",
                "parent": "<wxautox4.ui.chatbox.ChatBox object at 0x0000023CA8BCB200>",
                "root": "<wxautox4 - WeChatSubWnd object(\"纯路人\")>",
                "sender": "纯路人",
                "type": "text" # https://plus.wxauto.org/docs/class/Message.html
             }
             群消息
             {
                  "attr": "friend",
                  "chat_info": {
                    "chat_type": "group",
                    "chat_name": "委员会",
                    "group_member_count": 6
                  },
                  "content": "@真爱粉",
                  "control": "<wxautox4.uia.uiautomation.ListItemControl object at 0x0000023CA8BD8CE0>",
                  "hash": "27913b5c034854e5a4d2268fd0e380d0",
                  "hash_text": "(77,360)@真爱粉",
                  "id": "2e8a2aff7f368555d9cc5bd317acc532",
                  "parent": "<wxautox4.ui.chatbox.ChatBox object at 0x0000023CA8BD8560>",
                  "root": "<wxautox4 - WeChatSubWnd object(\"委员会\")>",
                  "sender": "纯路人",
                  "type": "text"
            }
        """

        def internal_callback(raw_msg, chat):
            if not self._running:
                LOG.warning("Discarding SDK callback during shutdown: chat=%s msg_hash=%s",
                            chat_name, getattr(raw_msg, 'hash', ''))
                return
            try:
                LOG.info('--------------Start------------------')
                attr = getattr(raw_msg, 'attr', '')
                # 快速过滤：在消息转换之前过滤，减少不必要的处理
                if attr.lower() in _IGNORED_ATTRS:
                    if attr.lower() == 'self':
                        self._remember_self_media(chat_name, raw_msg)
                    LOG.info("ignored system message attr is [%s]", attr)
                    return
                LOG.info('------------ Raw message info ------------\n%s', self._dump_obj_attrs(raw_msg))
                LOG.info('------------ Raw chat info ------------\n%s', self._dump_obj_attrs(chat))

                self._keep_for_reply(chat_name, raw_msg)
                # 转换消息
                message = convert_message(raw_msg, chat_name, bot_id=self._bot_id, bot_name=self._self_name)
                LOG.info('Converted message: %r', message)
                LOG.info('---------------END-----------------')

                # 所有消息无脑转发给 server，由 server 负责存储和路由
                callback(message, chat_name)
            except Exception:
                LOG.exception("Error in message callback for [%s]: msg_hash=%s",
                              chat_name, getattr(raw_msg, 'hash', ''))
                self._notify_broken_message(chat_name)

        return internal_callback

    def _notify_broken_message(self, chat_name: str) -> None:
        """消息转换或处理出错时提示对方重发，避免用户感觉假死"""
        try:
            result = self.wx.SendMsg(BROKEN_MESSAGE_REPLY, chat_name)
            self._check_response(result, "SendCallbackErrorNotification", chat_name)
        except Exception:
            LOG.exception("Failed to send callback error notification to [%s]", chat_name)

    def add_message_listener(self, chat_name: str, callback: MessageCallback) -> bool:
        """添加消息监听器"""
        with self._lifecycle_lock:
            if not self._running:
                LOG.info("Skipping listener registration during shutdown: %s", chat_name)
                return False
            try:
                LOG.info("Registering listener for [%s]", chat_name)

                internal_callback = self._create_internal_callback(chat_name, callback)
                with ui_transaction():
                    if self.wx.GetSubWindow(chat_name) is None:
                        self._pop_out(chat_name)
                    result = self.wx.AddListenChat(chat_name, internal_callback)
                if not self._check_response(result, "AddListenChat", chat_name):
                    return False
                # AddListenChat 可能报成功但聊天窗口没有弹出来，这种监听收不到任何消息
                window = self.wx.GetSubWindow(chat_name)
                if window is None:
                    LOG.error("[AddListenChat] [%s] reported success but its chat window is missing", chat_name)
                    return False
                self._listen_windows[chat_name] = window._api.HWND
                return True
            except Exception:
                LOG.exception("Failed to add listener for [%s]", chat_name)
                return False

    def _pop_out(self, chat_name: str) -> None:
        """
        用会话右键菜单的"独立窗口显示"把聊天弹成独立窗口，AddListenChat 看到窗口已经在了会直接接管

        SDK 自己是双击会话来弹窗口的。同一个进程里关过这个聊天的窗口后，双击经常没反应，
        SDK 等不到窗口就报 MoveWindow 1400，重置监听因此加不回去。右键菜单弹窗口在 ser 上实测每次都成功。
        弹不出来也不报错，交给 SDK 自己再双击一次。
        """
        try:
            import win32gui

            _init_uia_in_thread()
            session = self._session(chat_name)
            if session is None:
                self.wx.ChatWith(chat_name)
                session = self._session(chat_name)
            if session is None or not session.select_option(_POP_OUT_OPTION):
                LOG.warning("[%s] is not in the session list or has no pop-out option", chat_name)
                return
            window_class = win32gui.GetClassName(self.wx._api.HWND)
            deadline = time.monotonic() + _POP_OUT_WAIT
            while not win32gui.FindWindow(window_class, chat_name):
                if time.monotonic() >= deadline:
                    LOG.warning("[%s] chat window did not pop out", chat_name)
                    return
                time.sleep(0.1)
        except Exception:
            LOG.warning("Failed to pop out the chat window of [%s]", chat_name, exc_info=True)

    def listen_health(self, chat_names: list[str]) -> dict[str, Optional[str]]:
        """
        每个监听是否健康：注册时弹出的聊天窗口还在，标题也还是这个聊天对象

        只读窗口句柄，不碰界面、不用抢界面锁。

        Returns:
            {聊天对象: 不健康的原因，健康时为 None}
        """
        result = {}
        for name in chat_names:
            hwnd = self._listen_windows.get(name)
            if not hwnd:
                result[name] = "not_listening"
            elif not _window_titled(hwnd, name):
                result[name] = "window_not_found"
            else:
                result[name] = None
        return result

    def probe_listen(self, chat_name: str) -> Optional[dict]:
        """测活：读一遍聊天窗口里的消息，只返回条数和最后一条；没有这个聊天窗口时返回 None"""
        window = self.wx.GetSubWindow(chat_name)
        if window is None:
            return None
        messages = window.GetAllMessage()
        # 读失败时 SDK 不抛异常，返回一个"失败"的 WxResponse（是 dict 不是 list）
        if not isinstance(messages, list):
            reason = messages.get('message') if isinstance(messages, dict) else messages
            raise RuntimeError(f"GetAllMessage failed: {reason}")
        last = messages[-1] if messages else None
        return {
            "count": len(messages),
            "last": {"sender": last.sender, "type": last.type, "content": last.content} if last else None,
        }

    # ==================== 私聊轮询 ====================

    def next_private_messages(self) -> Optional[list[ChatMsg]]:
        """
        点开主窗口里下一个有红点的会话，取出新消息；只留私聊，其他会话的红点点掉就算了

        开了子窗口的聊天不出红点，这里拿不到；免打扰的会话直接跳过，所以群都设成免打扰就不会被点开。

        Returns:
            没有红点时返回 None；点开了一个会话时返回其中的私聊消息（不是私聊时是空列表）
        """
        _init_uia_in_thread()
        with ui_transaction():
            result = self.wx.GetNextNewMessage(filter_mute=True)
        if not result:
            # 没有红点时是空的；读失败时是一个"失败"的 WxResponse
            if isinstance(result, dict) and result.get("message"):
                LOG.warning("Private poll failed: %s", result.get("message"))
            return None
        chat_name = result.get("chat_name", "")
        raw_msgs = result.get("msg") or []
        try:
            return self._polled_messages(chat_name, result.get("chat_type", ""), raw_msgs)
        except Exception:
            # 红点已经点掉，这些消息不会再出现
            LOG.exception("Private poll failed to handle %d messages from [%s]", len(raw_msgs), chat_name)
            return []

    def _polled_messages(self, chat_name: str, chat_type: str, raw_msgs: list) -> list[ChatMsg]:
        if chat_type != "friend":
            # 群该设成免打扰；点掉的红点里有消息就丢了，打 warning 方便发现
            log = LOG.warning if raw_msgs else LOG.info
            log("Private poll skipped %d messages from [%s] (chat_type=%r)", len(raw_msgs), chat_name, chat_type)
            return []
        messages = []
        broken = 0
        for raw_msg in raw_msgs:
            if str(getattr(raw_msg, "attr", "")).lower() in _IGNORED_ATTRS:
                continue
            try:
                messages.append(convert_message(raw_msg, chat_name, bot_id=self._bot_id, bot_name=self._self_name))
            except Exception:
                broken += 1
                LOG.exception("Failed to convert a polled message from [%s]: msg_hash=%s",
                              chat_name, getattr(raw_msg, "hash", ""))
        if broken:
            self._notify_broken_message(chat_name)
        LOG.info("Private poll got %d messages from [%s]", len(messages), chat_name)
        return messages

    # ==================== 会话设置 ====================

    def mute_all_groups(self) -> dict:
        """
        把主窗口会话列表里的群都设成消息免打扰；不活跃、不在会话列表里的群不管

        会话列表不标注是群还是私聊，要逐个点开没免打扰的会话看一眼。有未读的先跳过：
        点开会把私聊的红点点掉，私聊轮询就收不到了；下次再点一键免打扰时补上。

        Returns:
            {"total": 会话数, "muted": [新设的群], "already": [本来就免打扰的会话],
             "unread": [有未读先跳过的], "failed": [{"chat", "reason"}]}
        """
        muted, already, unread, failed = [], [], [], []
        _init_uia_in_thread()
        with ui_transaction():
            self.wx.SwitchToChat()
            names = [s.name for s in self.wx.GetSession() or []]
            for name in names:
                try:
                    session = self._session(name)
                    if session is None:
                        continue
                    if session.ismute:
                        already.append(name)
                        continue
                    if session.new_count:
                        unread.append(name)
                        continue
                    session.click()
                    if (self.wx.ChatInfo() or {}).get("chat_type") != "group":
                        continue
                    if not self._session(name).select_option(_MUTE_OPTION):
                        failed.append({"chat": name, "reason": "右键菜单里没有免打扰"})
                    elif self._wait_muted(name):
                        muted.append(name)
                    else:
                        failed.append({"chat": name, "reason": "设置后仍不是免打扰"})
                except Exception as e:
                    LOG.exception("Failed to mute group [%s]", name)
                    failed.append({"chat": name, "reason": str(e)})
        LOG.info("Muted groups in %d sessions: muted=%s already=%d unread=%s failed=%s",
                 len(names), muted, len(already), unread, failed)
        return {"total": len(names), "muted": muted, "already": already, "unread": unread, "failed": failed}

    def accept_new_friends(self) -> list[str]:
        """
        通过通讯录里所有待通过的好友申请，最后切回聊天页

        Returns:
            通过了的申请（申请条目上的文字，含昵称和验证消息）
        """
        accepted = []
        _init_uia_in_thread()
        with ui_transaction():
            try:
                for request in self.wx.GetNewFriends(acceptable=True) or []:
                    text = str(getattr(request, "content", "") or "")
                    try:
                        result = request.accept()
                    except Exception:
                        LOG.exception("Failed to accept friend request [%s]", text)
                        continue
                    # accept 返回 WxResponse，失败时为假
                    if not result:
                        LOG.warning("Failed to accept friend request [%s]: %s", text,
                                    result.get('message') if isinstance(result, dict) else result)
                        continue
                    accepted.append(text)
            finally:
                self.wx.SwitchToChat()
        if accepted:
            LOG.info("Accepted %d friend requests: %s", len(accepted), accepted)
        return accepted

    def _wait_muted(self, name: str, seconds: float = 3.0) -> bool:
        """设完免打扰后会话列表要过一会儿才显示出来，等它变过来"""
        deadline = time.monotonic() + seconds
        while True:
            if getattr(self._session(name), "ismute", False):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.3)

    def _session(self, name: str):
        """主窗口会话列表里名字相同的会话，没有返回 None"""
        return next((s for s in self.wx.GetSession() or [] if s.name == name), None)

    # ==================== 生命周期 ====================

    def cleanup(self) -> None:
        """停止 SDK 监听，保留微信窗口供已接收的任务收尾。"""
        with self._lifecycle_lock:
            if not self._running:
                LOG.debug("Skipping cleanup: client is already stopping or stopped")
                return
            self._running = False
            wx = self._wx
        if wx is None:
            LOG.info("WxAutoClient cleaned up while offline")
            return
        # Do not hold the lifecycle lock while the SDK stops its listener threads.
        try:
            wx.StopListening(remove=False)
        except Exception:
            LOG.exception("Failed to stop wxautox4 listening")
            raise
        LOG.info("WxAutoClient cleaned up")
