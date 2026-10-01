# -*- coding: utf-8 -*-
"""
WeChatClient - 微信 base 的专属接口

在统一协议之外，微信 base 还开放了直接操作 wxautox4 的接口，监听管理用它们：

    POST {callback}/listen/add              {"nickname"}
    POST {callback}/settings                {"private_poll"?, "auto_accept_friends"?, "group_reply"?}
    POST {callback}/groups/mute-all         {}
    POST {callback}/execute/wx              {"name", "params"}
    POST {callback}/execute/chat            {"chat_name", "name", "params"}
    POST {callback}/execute/batch-chat-info {"chat_names"}
"""

import logging

from ._client import BaseClient

LOG = logging.getLogger("WeChatClient")

# base 加监听失败时会重试 3 次（实测 11 秒多），要等它返回真实结果
_LISTEN_ADD_TIMEOUT = (2, 30)
# 一键群免打扰要逐个点开会话列表里的会话，会话多时要等一会儿
_MUTE_ALL_TIMEOUT = (2, 300)


class WeChatClient(BaseClient):

    def __init__(self, bot):
        super().__init__(bot)
        self.log = LOG

    async def _call(self, label: str, path: str, payload: dict, timeout=None) -> dict:
        """调微信专属接口，返回 {"success", "data", "message"}，不抛异常"""
        try:
            res = await self._post(path, payload, **({"timeout": timeout} if timeout else {}))
            res.raise_for_status()
            result = res.data or {}
            return {"success": result.get("code") == 0, "data": result.get("data"),
                    "message": result.get("message", "")}
        except Exception as e:
            LOG.error("WeChat %s on bot [%s] failed: %s", label, self.bot.bot_id, e)
            return {"success": False, "data": None, "message": str(e)}

    async def add_listen_chat(self, nickname: str) -> dict:
        return await self._call("add_listen_chat", "/listen/add", {"nickname": nickname}, timeout=_LISTEN_ADD_TIMEOUT)

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
        return await self._call("probe_listen", "/listen/probe", {"chat_name": chat_name})
