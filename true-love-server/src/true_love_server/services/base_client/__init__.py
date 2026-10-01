# -*- coding: utf-8 -*-
"""
Base Client - server 调 base

所有平台的 base 实现同一套协议（见 _client.py），server 按机器人登记的回调地址调用：

    from ..services import base_client

    await base_client.send_text(bot_id, receiver, at_user, content)
    await base_client.send_file(bot_id, url, receiver)
    await base_client.send_to_master(bot_id, content)   # bot_id 为空时发给默认机器人的管理员

    # 微信专属操作：机器人不是微信时抛 NotSupported
    wechat = base_client.wechat(bot_id)
    await wechat.execute_wx("GetAllSubWindow", {})

新增平台：写一个实现同一套协议的 base 就行，server 不用改；要加渠道专属接口时，
仿照 _wechat.py 加一个客户端，并在 bot_registry.CAPABILITIES 里登记能力。
"""

import logging

from ._client import BaseClient
from ._wechat import WeChatClient
from .. import bot_registry

LOG = logging.getLogger("BaseClient")

__all__ = ["BaseClient", "WeChatClient", "NotSupported", "for_bot", "wechat",
           "send_text", "send_file", "send_to_master"]


class NotSupported(ValueError):
    """机器人所在的平台没有这个功能"""


def for_bot(bot_id: str) -> BaseClient:
    """这个机器人的 base。Raises: bot_registry.UnknownBot"""
    return BaseClient(bot_registry.get(bot_id))


def wechat(bot_id: str) -> WeChatClient:
    """这个微信机器人的 base。Raises: bot_registry.UnknownBot、NotSupported"""
    bot = bot_registry.get(bot_id)
    if not bot.can("listen"):
        raise NotSupported(f"机器人 {bot_id} 是 {bot.platform}，不支持微信专属操作")
    return WeChatClient(bot)


# ==================== 所有平台通用的快捷函数，出错时返回 (False, 原因)，不抛异常 ====================

async def send_text(bot_id: str, receiver: str, at_user: str, content: str,
                    raise_on_error: bool = False, reply_msg_id: str = "") -> tuple[bool, str]:
    try:
        return await for_bot(bot_id).send_text(receiver, at_user, content, raise_on_error=raise_on_error,
                                               reply_msg_id=reply_msg_id)
    except Exception as e:
        if raise_on_error:
            raise
        LOG.warning("send_text via bot [%s] failed: %s", bot_id, e)
        return False, str(e)


async def send_file(bot_id: str, url: str, receiver: str, raise_on_error: bool = False) -> tuple[bool, str]:
    try:
        return await for_bot(bot_id).send_file(url, receiver, raise_on_error=raise_on_error)
    except Exception as e:
        if raise_on_error:
            raise
        LOG.warning("send_file via bot [%s] failed: %s", bot_id, e)
        return False, str(e)


async def send_to_master(bot_id: str, content: str) -> tuple[bool, str]:
    """给机器人的管理员发通知；bot_id 为空时用默认机器人"""
    try:
        return await BaseClient(bot_registry.resolve(bot_id)).send_to_master(content)
    except Exception as e:
        LOG.warning("send_to_master via bot [%s] failed: %s", bot_id, e)
        return False, str(e)
