# -*- coding: utf-8 -*-
"""
Open Routes - 给外部调用方的接口

- /ping、/health：存活检查
- /send-msg：外部推送通知给管理员（部署结果等），不指定机器人时从默认机器人发，和合并前一样
"""

import logging

from fastapi import APIRouter

from .deps import verify_token
from .exception_handlers import ApiResponse, ValidationException
from ..services import base_client

LOG = logging.getLogger("OpenRoutes")

open_router = APIRouter()

# 外部推送接口里代表管理员的接收者
MASTER = "master"


@open_router.get("/ping")
async def ping():
    """简单存活检查"""
    return "pong"


@open_router.get("/health")
async def health():
    """Docker 健康检查接口"""
    return {"status": "ok", "service": "true-love-server"}


@open_router.post("/send-msg")
async def send_msg(request: dict):
    """
    推送消息接口

    供外部调用，给管理员推送通知（部署结果等）。接收者只能是 master，管理员具体是谁由机器人的 base 决定。

    Body:
        - token:        鉴权 token
        - sendReceiver: 固定为 "master"
        - content:      消息内容
        - bot_id:       从哪个机器人发（可选），不传用默认机器人
    """
    LOG.info("推送消息收到请求, req: %s", {k: v for k, v in request.items() if k != "token"})

    verify_token(request.get('token', ''))

    send_receiver = request.get('sendReceiver')
    content = request.get('content')

    if send_receiver != MASTER or not content:
        raise ValidationException("诶嘿~接收者没注册或者内容是空的呢，检查一下吧~")

    success, error_msg = await base_client.send_to_master(request.get("bot_id") or "", content)

    if not success:
        raise ValidationException(f"呜呜~消息发送失败了捏: {error_msg}")

    return ApiResponse(data=None)
