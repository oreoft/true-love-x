# -*- coding: utf-8 -*-
"""WeChat Base 客户端实现"""

import json
import logging

from true_love_common.hosts import machine_bot_id, server_host
from true_love_common.http.client import HttpResult, async_post, trace_headers
from true_love_common.media import to_url

from ._interface import BaseClient, api_response_ok

LOG = logging.getLogger("WeChatBaseClient")
_TIMEOUT = (2, 10)
_FILE_TIMEOUT = (2, 60)  # 文件发送涉及 WeChat UI 自动化操作（含重试），比普通请求慢


class WeChatBaseClient(BaseClient):

    async def _post(self, label: str, url: str, payload: str, timeout=_TIMEOUT) -> HttpResult:
        return await async_post(
            url,
            headers=trace_headers({"Content-Type": "application/json"}),
            data=payload,
            timeout=timeout,
        )

    # ==================== 通用接口实现 ====================

    async def send_text(self, receiver: str, at_user: str, content: str,
                        raise_on_error: bool = False) -> tuple[bool, str]:
        url = f"{self.host}/send/text"
        payload = json.dumps({"sendReceiver": receiver, "atReceiver": at_user, "content": content},
                             ensure_ascii=False)
        try:
            return api_response_ok(await self._post("send_text", url, payload))
        except Exception as e:
            LOG.error("WeChat send_text failed: %s", e)
            if raise_on_error:
                raise
            return False, str(e)

    async def send_file(self, ref: str, receiver: str,
                        raise_on_error: bool = False) -> tuple[bool, str]:
        """发送文件。ref 是文件的 URL，wx base 自己下载后发送。"""
        try:
            url = f"{self.host}/send/file"
            payload = json.dumps({"url": ref, "sendReceiver": receiver}, ensure_ascii=False)
            return api_response_ok(await self._post("send_file", url, payload, timeout=_FILE_TIMEOUT))
        except Exception as e:
            LOG.error("WeChat send_file failed: %s", e)
            if raise_on_error:
                raise
            return False, str(e)

    # ==================== WeChat 专属操作 ====================

    async def send_to_master(self, content: str) -> tuple[bool, str]:
        """给这台机器的管理员发文本。管理员是谁只有 base 知道，这里不传接收者。"""
        payload = json.dumps({"is_master": True, "content": content}, ensure_ascii=False)
        try:
            return api_response_ok(await self._post("send_to_master", f"{self.host}/send/text", payload))
        except Exception as e:
            LOG.error("WeChat send_to_master failed: %s", e)
            return False, str(e)

    async def send_img(self, path: str, receiver: str, raise_on_error: bool = False) -> tuple[bool, str]:
        """发送这台 server 自己的图片（path 相对工作目录），base 从 server 的 /media 下载"""
        url = to_url(path, server_host(machine_bot_id()))
        return await self.send_file(url, receiver, raise_on_error=raise_on_error)

    async def add_listen_chat(self, nickname: str) -> dict:
        payload = json.dumps({"nickname": nickname}, ensure_ascii=False)
        try:
            res = await self._post("add_listen_chat", f"{self.host}/listen/add", payload)
            res.raise_for_status()
            result = res.data or {}
            return {"success": result.get("code") == 0, "data": result.get("data"), "message": result.get("message", "")}
        except Exception as e:
            LOG.error("WeChat add_listen_chat failed: %s", e)
            return {"success": False, "data": None, "message": str(e)}

    async def execute_wx(self, method_name: str, params: dict = None) -> dict:
        payload = json.dumps({"name": method_name, "params": params or {}}, ensure_ascii=False)
        try:
            res = await self._post("execute_wx", f"{self.host}/execute/wx", payload)
            res.raise_for_status()
            result = res.data or {}
            return {"success": result.get("code") == 0, "data": result.get("data"), "message": result.get("message", "")}
        except Exception as e:
            LOG.error("WeChat execute_wx failed: %s", e)
            return {"success": False, "data": None, "message": str(e)}

    async def execute_chat(self, chat_name: str, method_name: str, params: dict = None) -> dict:
        payload = json.dumps({"chat_name": chat_name, "name": method_name, "params": params or {}},
                             ensure_ascii=False)
        try:
            res = await self._post("execute_chat", f"{self.host}/execute/chat", payload)
            res.raise_for_status()
            result = res.data or {}
            return {"success": result.get("code") == 0, "data": result.get("data"), "message": result.get("message", "")}
        except Exception as e:
            LOG.error("WeChat execute_chat failed: %s", e)
            return {"success": False, "data": None, "message": str(e)}

    async def batch_chat_info(self, chat_names: list[str]) -> dict:
        if not chat_names:
            return {"success": True, "data": {"results": {}}, "message": ""}
        payload = json.dumps({"chat_names": chat_names}, ensure_ascii=False)
        try:
            res = await self._post("batch_chat_info", f"{self.host}/execute/batch-chat-info", payload)
            res.raise_for_status()
            result = res.data or {}
            return {"success": result.get("code") == 0, "data": result.get("data"), "message": result.get("message", "")}
        except Exception as e:
            LOG.error("WeChat batch_chat_info failed: %s", e)
            return {"success": False, "data": None, "message": str(e)}
