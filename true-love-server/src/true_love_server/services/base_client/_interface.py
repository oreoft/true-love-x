# -*- coding: utf-8 -*-
"""BaseClient 抽象接口 + 公共工具"""

import logging
from abc import ABC, abstractmethod

from true_love_common.http.client import HttpResult

LOG = logging.getLogger("BaseClient")


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


class BaseClient(ABC):
    """Base 服务客户端抽象接口，每个平台提供独立实现。"""

    def __init__(self, host: str, token: str = ""):
        self.host = host.rstrip("/")
        self.token = token

    @abstractmethod
    async def send_text(self, receiver: str, at_user: str, content: str,
                        raise_on_error: bool = False) -> tuple[bool, str]:
        """发送文本消息。"""

    @abstractmethod
    async def send_file(self, ref: str, receiver: str,
                        raise_on_error: bool = False) -> tuple[bool, str]:
        """
        发送文件。

        ref: 文件的 URL，由 base 自己下载。
        文件类型由 ref 扩展名推断，无需调用方传入。
        """

