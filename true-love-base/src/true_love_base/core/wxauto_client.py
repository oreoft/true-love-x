# -*- coding: utf-8 -*-
"""
WxAuto WxAutoClient - wxautox4 SDK 封装客户端

封装 wxautox4 的具体调用。
"""

import json
import logging
from datetime import datetime
from threading import Lock
from typing import Callable, Optional

# This is a special import, please do not modify
from true_love_base.wxautox4x.wxautox4x import WeChat
from wxautox4.param import WxParam

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
    LOG.info(f"Set WxParam.DEFAULT_SAVE_PATH to: {_wx_imgs_dir}")

MessageCallback = Callable[[ChatMsg, str], None]


class WxAutoClient():
    """
    wxautox4 客户端适配器 封装 wxautox4 的所有操作。
    """

    def __init__(self, bot_id: str = "", account_of: Optional[Callable[[], str]] = None):
        """
        创建客户端；不连接微信，微信可用时由 connect() 建立连接

        Args:
            bot_id: 这个机器人的标识（微信是 wxid），随每条消息带给下游
            account_of: 读出当前登录的微信号；给了就在连接时核对，登录的不是 bot_id 这个号就不接管
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

    def connect(self) -> bool:
        """连接已登录的微信主窗口；微信没开或没登录时返回 False，由调用方稍后重试"""
        try:
            wx = WeChat(version='WeChat')
            # 主窗口还在但已经掉线（比如断网）时先不接管，等它恢复在线
            if not wx.IsOnline():
                raise RuntimeError("WeChat main window is open but not online")
            self._check_account()
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
        LOG.info("WxAutoClient connected, self: %s", self.get_self_name())
        return True

    def _check_account(self) -> None:
        """登录的号和部署时指定的不一致时抛异常，避免用别人的身份收发消息"""
        if self._account_of is None:
            return
        account = self._account_of()
        if account != self._bot_id:
            raise RuntimeError(f"WeChat is logged in as {account}, but this base runs bot {self._bot_id}")

    def disconnect(self) -> None:
        """微信掉线后丢弃当前实例；监听在下一次 connect() 之后重新注册"""
        # Wait for an in-flight registration, so it cannot land on the instance being dropped.
        with self._lifecycle_lock:
            wx, self._wx = self._wx, None
        if wx is None:
            return
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
            LOG.info(f"[{action}] [{target}] succeeded")
            return True
        else:
            # WxResponse 失败时，通过 result['message'] 获取错误信息
            error_msg = result.get('message', 'Unknown error') if isinstance(result, dict) else str(result)
            LOG.error(f"[{action}] [{target}] failed: {error_msg}")
            return False

    # ==================== 账号信息 ====================

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

    def send_text(self, receiver: str, content: str, at_list: Optional[list[str]] = None) -> bool:
        """发送文本消息"""
        try:
            LOG.debug(f"SendMsg content: {content[:50]}...")
            sub_window = self.wx.GetSubWindow(receiver)
            result = (
                sub_window.SendMsg(content, at=at_list)
                if sub_window
                else self.wx.SendMsg(content, receiver, at=at_list)
            )
            return self._check_response(result, "SendMsg", receiver)
        except Exception as e:
            LOG.error(f"Failed to send text to [{receiver}]: {e}")
            return False

    _AUDIO_EXTS = (".wav", ".mp3")

    def send_file(self, receiver: str, file_path: str) -> bool:
        """发送文件；音频文件（.wav/.mp3）走原生语音气泡 SendAudio，失败直接返回失败，不降级为普通文件"""
        if file_path.lower().endswith(self._AUDIO_EXTS):
            try:
                return self._send_audio(receiver, file_path)
            except Exception as e:
                LOG.error(f"SendAudio failed for [{receiver}]: {e}")
                return False
        return self._send_file_generic(receiver, file_path)

    def _send_audio(self, receiver: str, file_path: str) -> bool:
        """[Beta] 发送语音条消息，需要 4.1.9+ 客户端"""
        LOG.debug(f"SendAudio path: {file_path}")
        sub_window = self.wx.GetSubWindow(receiver)
        result = sub_window.SendAudio(file_path) if sub_window else self.wx.SendAudio(file_path, who=receiver)
        return self._check_response(result, "SendAudio", receiver)

    def _send_file_generic(self, receiver: str, file_path: str) -> bool:
        try:
            LOG.debug(f"SendFiles path: {file_path}")
            sub_window = self.wx.GetSubWindow(receiver)
            result = sub_window.SendFiles(file_path) if sub_window else self.wx.SendFiles(file_path, receiver)
            return self._check_response(result, "SendFiles", receiver)
        except Exception as e:
            LOG.error(f"Failed to send file to [{receiver}]: {e}")
            return False

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
                LOG.debug("Discarding SDK callback during shutdown: chat=%s", chat_name)
                return
            try:
                LOG.info('--------------Start------------------')
                attr = getattr(raw_msg, 'attr', '')
                # 快速过滤：在消息转换之前过滤，减少不必要的处理
                if attr.lower() in ['weixin', 'system', 'self']:
                    LOG.info(f"ignored system message attr is [{attr}]")
                    return
                LOG.info('------------ Raw message info ------------\n%s', self._dump_obj_attrs(raw_msg))
                LOG.info('------------ Raw chat info ------------\n%s', self._dump_obj_attrs(chat))

                # 转换消息
                message = convert_message(raw_msg, chat_name, bot_id=self._bot_id, bot_name=self._self_name)
                LOG.info('Converted message: %r', message)
                LOG.info('---------------END-----------------')

                # 所有消息无脑转发给 server，由 server 负责存储和路由
                callback(message, chat_name)
            except Exception:
                LOG.exception("Error in message callback for [%s]", chat_name)
                # 发送错误提示，避免用户感觉假死
                try:
                    result = self.wx.SendMsg("啊咧？消息好像坏掉了，麻烦再发一次吧~", chat_name)
                    self._check_response(result, "SendCallbackErrorNotification", chat_name)
                except Exception:
                    LOG.exception("Failed to send callback error notification to [%s]", chat_name)

        return internal_callback

    def add_message_listener(self, chat_name: str, callback: MessageCallback) -> bool:
        """添加消息监听器"""
        with self._lifecycle_lock:
            if not self._running:
                LOG.info("Skipping listener registration during shutdown: %s", chat_name)
                return False
            try:
                LOG.info(f"Registering listener for [{chat_name}]")

                internal_callback = self._create_internal_callback(chat_name, callback)
                result = self.wx.AddListenChat(chat_name, internal_callback)
                if not self._check_response(result, "AddListenChat", chat_name):
                    return False
                # AddListenChat 可能报成功但聊天窗口没有弹出来，这种监听收不到任何消息
                if self.wx.GetSubWindow(chat_name) is None:
                    LOG.error(f"[AddListenChat] [{chat_name}] reported success but its chat window is missing")
                    return False
                return True
            except Exception:
                LOG.exception("Failed to add listener for [%s]", chat_name)
                return False

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
