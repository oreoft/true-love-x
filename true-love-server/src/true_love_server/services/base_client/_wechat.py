# -*- coding: utf-8 -*-
"""
WeChatClient - 微信 base 的专属接口

在统一协议之外，微信 base 还开放了直接操作 wxautox4 的接口，监听管理用它们：

    POST {callback}/listen/add              {"nickname"}
    POST {callback}/settings                {"private_poll"?, "auto_accept_friends"?, "group_reply"?}
    POST {callback}/groups/mute-all         {}
    POST {callback}/execute/wx              {"name", "params"}
    POST {callback}/execute/chat            {"chat_name", "name", "params"}
    POST {callback}/listen/status            {"chat_names"}
    POST {callback}/listen/probe             {"chat_name"}

tl-admin 的聊天页和好友页直接操作机器人的微信：

    POST {callback}/chat/sessions           {}
    POST {callback}/chat/messages           {"chat_name", "history"?}
    POST {callback}/chat/quote              {"chat_name", "msg_id", "content"}
    POST {callback}/chat/tickle             {"chat_name", "msg_id"}
    POST {callback}/chat/media              {"chat_name", "msg_id", "quoted"?}，下载好的文件再从 GET /media/{path} 取
    POST {callback}/friends/requests        {}
    POST {callback}/friends/accept          {"content", "remark"?}
    POST {callback}/friends/add             {"keywords", "addmsg"?, "remark"?}
    POST {callback}/friends/edit            {"chat_name", "remark"}
"""

import logging
import posixpath

from . import _client
from ._client import BaseClient

LOG = logging.getLogger("WeChatClient")

# base 加监听失败时会重试 3 次（实测 11 秒多），要等它返回真实结果
_LISTEN_ADD_TIMEOUT = (2, 30)
# 一键群免打扰要逐个点开会话列表里的会话，会话多时要等一会儿
_MUTE_ALL_TIMEOUT = (2, 300)
# 测活要读一遍聊天窗口里的消息，m8s 实测 9~14 秒
_PROBE_TIMEOUT = (2, 30)
# 后台聊天页读消息、往上翻历史、拍一拍和引用都要操作聊天窗口，界面锁被私聊轮询或发消息占着时还要排队
_CHAT_TIMEOUT = (2, 60)
# 好友申请在通讯录里，要切页面、逐条读，加好友还要等搜索结果
_FRIENDS_TIMEOUT = (2, 90)
# 后台点开图片、视频、文件：base 在微信里点开下载，再把文件传过来
_MEDIA_TIMEOUT = (2, 120)

_SDK_OK = "成功"


def sdk_failure(data) -> str | None:
    """
    /execute/* 只要方法没抛异常就回 code=0，SDK 的执行结果原样放在 data 里。
    SDK 返回 WxResponse（dict 子类）时序列化成 {"status": "成功"/"失败"/"错误", "message", "data"}，
    status 不是"成功"就是失败，返回失败原因；其他返回值不算失败，返回 None。
    """
    if not isinstance(data, dict) or not {"status", "message"} <= data.keys():
        return None
    if data["status"] == _SDK_OK:
        return None
    return f"{data['status']}: {data.get('message') or 'no message'}"


class WeChatClient(BaseClient):

    def __init__(self, bot):
        super().__init__(bot)
        self.log = LOG

    async def _call(self, label: str, path: str, payload: dict, timeout=None) -> dict:
        """调微信专属接口，返回 {"success", "data", "message"}，不抛异常"""
        try:
            res = await self._post(path, payload, **({"timeout": timeout} if timeout else {}))
            res.raise_for_status()
        except Exception as e:
            LOG.warning("WeChat %s on bot [%s] failed: %s", label, self.bot.bot_id, e)
            return {"success": False, "data": None, "message": str(e)}
        result = res.data if isinstance(res.data, dict) else {}
        data = result.get("data")
        if result.get("code") != 0:
            message = result.get("message") or str(result)
            LOG.warning("WeChat %s on bot [%s] failed: code=%s message=%s",
                        label, self.bot.bot_id, result.get("code"), message)
            return {"success": False, "data": data, "message": message}
        sdk_error = sdk_failure(data)
        if sdk_error is not None:
            LOG.warning("WeChat %s on bot [%s] failed in SDK: %s", label, self.bot.bot_id, sdk_error)
            return {"success": False, "data": data, "message": sdk_error}
        return {"success": True, "data": data, "message": result.get("message", "")}

    async def add_listen_chat(self, nickname: str) -> dict:
        result = await self._call("add_listen_chat", "/listen/add", {"nickname": nickname}, timeout=_LISTEN_ADD_TIMEOUT)
        # base 请求成功但加监听失败时回 {"success": false}
        if result["success"] and not (result["data"] or {}).get("success"):
            return {**result, "success": False, "message": "base failed to add the listener"}
        return result

    async def apply_settings(self, settings: dict) -> dict:
        return await self._call("apply_settings", "/settings", settings)

    async def mute_all_groups(self) -> dict:
        return await self._call("mute_all_groups", "/groups/mute-all", {}, timeout=_MUTE_ALL_TIMEOUT)

    async def execute_wx(self, method_name: str, params: dict = None) -> dict:
        return await self._call("execute_wx", "/execute/wx", {"name": method_name, "params": params or {}})

    async def execute_chat(self, chat_name: str, method_name: str, params: dict = None) -> dict:
        payload = {"chat_name": chat_name, "name": method_name, "params": params or {}}
        return await self._call("execute_chat", "/execute/chat", payload)

    async def listen_status(self, chat_names: list[str]) -> dict:
        return await self._call("listen_status", "/listen/status", {"chat_names": chat_names})

    async def probe_listen(self, chat_name: str) -> dict:
        return await self._call("probe_listen", "/listen/probe", {"chat_name": chat_name}, timeout=_PROBE_TIMEOUT)

    # ==================== 后台聊天和好友 ====================

    async def sessions(self) -> dict:
        return await self._call("sessions", "/chat/sessions", {}, timeout=_CHAT_TIMEOUT)

    async def chat_messages(self, chat_name: str, history: int = 0) -> dict:
        payload = {"chat_name": chat_name, "history": history}
        return await self._call("chat_messages", "/chat/messages", payload, timeout=_CHAT_TIMEOUT)

    async def quote(self, chat_name: str, msg_id: str, content: str) -> dict:
        payload = {"chat_name": chat_name, "msg_id": msg_id, "content": content}
        return await self._call("quote", "/chat/quote", payload, timeout=_CHAT_TIMEOUT)

    async def tickle(self, chat_name: str, msg_id: str) -> dict:
        payload = {"chat_name": chat_name, "msg_id": msg_id}
        return await self._call("tickle", "/chat/tickle", payload, timeout=_CHAT_TIMEOUT)

    async def download_media(self, chat_name: str, msg_id: str, quoted: bool = False) -> dict:
        """
        让 base 下载一条图片、视频或文件消息，再从 base 的 /media 取回来

        Returns:
            {"success", "data": {"name": 文件名, "content": 文件内容}, "message"}
        """
        payload = {"chat_name": chat_name, "msg_id": msg_id, "quoted": quoted}
        result = await self._call("download_media", "/chat/media", payload, timeout=_MEDIA_TIMEOUT)
        path = str((result.get("data") or {}).get("path") or "") if result["success"] else ""
        if not path:
            return result if not result["success"] else {**result, "success": False, "message": "base 没给文件路径"}
        res = await _client.async_get(f"{self.host}/media/{path}", timeout=_MEDIA_TIMEOUT, quiet=True)
        if not res.ok:
            LOG.warning("Fetching %s from bot [%s] failed: %s %s", path, self.bot.bot_id, res.status_code, res.error)
            return {"success": False, "data": None, "message": f"取文件失败: {res.status_code} {res.error}"}
        return {"success": True, "data": {"name": posixpath.basename(path), "content": res.content}, "message": ""}

    async def friend_requests(self) -> dict:
        return await self._call("friend_requests", "/friends/requests", {}, timeout=_FRIENDS_TIMEOUT)

    async def accept_friend(self, request: dict) -> dict:
        return await self._call("accept_friend", "/friends/accept", request, timeout=_FRIENDS_TIMEOUT)

    async def add_friend(self, request: dict) -> dict:
        return await self._call("add_friend", "/friends/add", request, timeout=_FRIENDS_TIMEOUT)

    async def edit_friend(self, request: dict) -> dict:
        return await self._call("edit_friend", "/friends/edit", request, timeout=_FRIENDS_TIMEOUT)
