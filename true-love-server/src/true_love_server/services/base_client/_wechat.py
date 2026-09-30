# -*- coding: utf-8 -*-
"""
WeChatClient - 微信 base 的专属接口

在统一协议之外，微信 base 还开放了直接操作 wxautox4 的接口，监听管理用它们：

    POST {callback}/listen/add              {"nickname"}
    POST {callback}/execute/wx              {"name", "params"}
    POST {callback}/execute/chat            {"chat_name", "name", "params"}
    POST {callback}/execute/batch-chat-info {"chat_names"}
"""

import logging

from ._client import BaseClient

LOG = logging.getLogger("WeChatClient")


class WeChatClient(BaseClient):

    def __init__(self, bot):
        super().__init__(bot)
        self.log = LOG

    async def _call(self, label: str, path: str, payload: dict) -> dict:
        """调微信专属接口，返回 {"success", "data", "message"}，不抛异常"""
        try:
            res = await self._post(path, payload)
            res.raise_for_status()
            result = res.data or {}
            return {"success": result.get("code") == 0, "data": result.get("data"),
                    "message": result.get("message", "")}
        except Exception as e:
            LOG.error("WeChat %s on bot [%s] failed: %s", label, self.bot.bot_id, e)
            return {"success": False, "data": None, "message": str(e)}

    async def add_listen_chat(self, nickname: str) -> dict:
        return await self._call("add_listen_chat", "/listen/add", {"nickname": nickname})

    async def execute_wx(self, method_name: str, params: dict = None) -> dict:
        return await self._call("execute_wx", "/execute/wx", {"name": method_name, "params": params or {}})

    async def execute_chat(self, chat_name: str, method_name: str, params: dict = None) -> dict:
        payload = {"chat_name": chat_name, "name": method_name, "params": params or {}}
        return await self._call("execute_chat", "/execute/chat", payload)

    async def batch_chat_info(self, chat_names: list[str]) -> dict:
        if not chat_names:
            return {"success": True, "data": {"results": {}}, "message": ""}
        return await self._call("batch_chat_info", "/execute/batch-chat-info", {"chat_names": chat_names})
