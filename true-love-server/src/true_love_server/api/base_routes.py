# -*- coding: utf-8 -*-
"""
Base Routes - 给 base 调的接口（/base/*）

这是 server 对各平台 base 的统一协议的上行部分。每个请求体都带：
    - token: 鉴权 token
    - bot:   {"bot_id", "platform", "callback", "name"}，这个 base 跑的机器人（见 true_love_common.bot）

server 每次都按 bot 登记一遍：第一次见到就建好它的库，回调地址变了就更新。
下行部分（server 调 base）见 services/base_client。
"""

import logging

from fastapi import APIRouter, BackgroundTasks
from true_love_common.bot import BotInfo
from true_love_common.chat_msg import ChatMsg

from .deps import verify_token
from .exception_handlers import ApiResponse, ValidationException
from ..services import bot_registry, bot_settings, listen_store
from ..services.bot_registry import BotRecord
from ..services.message_service import handle_incoming

LOG = logging.getLogger("BaseRoutes")

base_router = APIRouter(prefix="/base")


def _register(request: dict) -> BotRecord:
    """校验 token 并登记请求里的机器人"""
    verify_token(request.get("token", ""))
    try:
        return bot_registry.register(BotInfo.from_dict(request.get("bot")))
    except ValueError as e:
        raise ValidationException(str(e))


@base_router.post("/register")
async def register(request: dict):
    """
    只登记，不做别的：base 启动或者回调地址变了时可以调一下

    Response:
        - data: 登记后的机器人信息
    """
    return ApiResponse(data=_register(request).to_dict())


@base_router.post("/on-message")
async def on_message(request: dict, background_tasks: BackgroundTasks):
    """
    消息统一入口，base 把收到的所有消息都转过来：
    - 所有消息存进机器人自己的库
    - @ 机器人或私聊的才交给 AI
    - 存储去重，重复消息不再触发 AI

    Body:
        - msg: ChatMsg.to_dict()，媒体是 base 上的相对路径，交给 AI 前换成 base 的 URL
    """
    bot = _register(request)
    msg = ChatMsg.from_dict(request.get("msg", {}))
    LOG.info("聊天消息收到请求: bot_id=%s chat=%s sender=%s type=%s",
             bot.bot_id, msg.chat_id, msg.sender_id, msg.msg_type)
    background_tasks.add_task(handle_incoming, bot, msg)
    return ApiResponse(data="")


@base_router.post("/listen/list")
async def listen_list(request: dict):
    """
    微信 base 连上微信时来取要监听的群和好友，以及是否轮询没开子窗口的私聊

    Response:
        - data: {"chats": [...], "private_poll": bool}
    """
    bot = _register(request)
    if not bot.can("listen"):
        raise ValidationException(f"机器人 {bot.bot_id} 是 {bot.platform}，没有监听功能")
    return ApiResponse(data={
        "chats": listen_store.list_all(bot.bot_id),
        "private_poll": bot_settings.get_bool(bot.bot_id, bot_settings.PRIVATE_POLL),
    })
