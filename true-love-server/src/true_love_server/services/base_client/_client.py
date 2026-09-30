# -*- coding: utf-8 -*-
"""
BaseClient - server 调 base 的统一协议

每个平台写一个 base 实现同一套接口，server 只用这一个客户端，按机器人登记的回调地址调用：

    POST {callback}/send/text   {"sendReceiver", "atReceiver", "content"} 或 {"is_master": true, "content"}
    POST {callback}/send/file   {"sendReceiver", "url"} 或 {"is_master": true, "url"}
    GET  {callback}/status      {"data": {"wx_online", "self_name", "since", "bot_id"}}
    GET  {callback}/media/...   base 收到的媒体文件

文件一律传 URL，由 base 自己下载；平台之间的能力差异（比如飞书只能发 URL）由各自的 base 消化。
管理员是谁只有 base 知道：发给管理员时传 is_master，不传接收者。
返回体是 {"code": 0 表示成功, "message", "data"}。
"""

import json
import logging

from true_love_common.http.client import HttpResult, async_get, async_post, trace_headers

from ..bot_registry import BotRecord

LOG = logging.getLogger("BaseClient")

_TIMEOUT = (2, 10)
_FILE_TIMEOUT = (2, 60)  # 发文件要先下载再操作客户端界面（含重试），比普通请求慢
_STATUS_TIMEOUT = (2, 3)


def api_response_ok(res: HttpResult) -> tuple[bool, str]:
    """检查 HTTP 和业务响应码。HTTP 200 但 code != 0 也算失败。"""
    if not res.ok:
        return False, res.error or res.text

    data = res.data
    if data is None:
        return True, ""

    if isinstance(data, dict):
        code = data.get("code", 0)
        if str(code) != "0":
            return False, data.get("message") or data.get("msg") or str(data)
        if data.get("success") is False:
            return False, data.get("message") or data.get("msg") or str(data)
    return True, ""


class BaseClient:
    """调一个机器人的 base"""

    def __init__(self, bot: BotRecord):
        self.bot = bot
        self.host = bot.callback.rstrip("/")
        self.log = LOG

    async def _post(self, path: str, payload: dict, timeout=_TIMEOUT) -> HttpResult:
        return await async_post(
            f"{self.host}{path}",
            headers=trace_headers({"Content-Type": "application/json"}),
            data=json.dumps(payload, ensure_ascii=False),
            timeout=timeout,
        )

    async def _send(self, label: str, path: str, payload: dict, timeout=_TIMEOUT,
                    raise_on_error: bool = False) -> tuple[bool, str]:
        try:
            return api_response_ok(await self._post(path, payload, timeout=timeout))
        except Exception as e:
            self.log.error("%s to bot [%s] failed: %s", label, self.bot.bot_id, e)
            if raise_on_error:
                raise
            return False, str(e)

    async def send_text(self, receiver: str, at_user: str, content: str,
                        raise_on_error: bool = False) -> tuple[bool, str]:
        payload = {"sendReceiver": receiver, "atReceiver": at_user, "content": content}
        return await self._send("send_text", "/send/text", payload, raise_on_error=raise_on_error)

    async def send_file(self, url: str, receiver: str, raise_on_error: bool = False) -> tuple[bool, str]:
        """发文件，url 由 base 自己下载"""
        payload = {"url": url, "sendReceiver": receiver}
        return await self._send("send_file", "/send/file", payload, timeout=_FILE_TIMEOUT,
                                raise_on_error=raise_on_error)

    async def send_to_master(self, content: str) -> tuple[bool, str]:
        """给这个机器人的管理员发文本。管理员是谁只有 base 知道，这里不传接收者。"""
        return await self._send("send_to_master", "/send/text", {"is_master": True, "content": content})

    async def status(self) -> dict:
        """base 报告的在线状态；连不上时返回 {"reachable": False}"""
        try:
            res = await async_get(f"{self.host}/status", timeout=_STATUS_TIMEOUT)
            if not res.status_code:  # 连接都没建立起来
                return {"reachable": False, "online": False, "message": res.error or res.text}
            ok, message = api_response_ok(res)
            if not ok:
                return {"reachable": True, "online": False, "message": message}
            data = (res.data or {}).get("data") or {}
            return {"reachable": True, "online": bool(data.get("wx_online")), "self_name": data.get("self_name"),
                    "since": data.get("since")}
        except Exception as e:
            return {"reachable": False, "online": False, "message": str(e)}
