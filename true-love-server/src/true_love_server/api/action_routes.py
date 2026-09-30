# -*- coding: utf-8 -*-
"""
Action Routes - AI 回调接口

AI 侧 Agent 执行 skill 后，通过这些接口操作 WeChat（发消息、管理提醒、管理监听）。
所有接口均需要 token 验证。
"""

import logging

from fastapi import APIRouter
from true_love_common.hosts import ai_host
from true_love_common.media import to_url

from .deps import verify_token
from .exception_handlers import ApiResponse, ValidationException
from .. import Config
from ..services import base_client
from ..services.listen_manager import get_listen_manager
from ..services import reminder_service

LOG = logging.getLogger("ActionRoutes")

action_router = APIRouter(prefix="/action")

listen_manager = get_listen_manager()


# ==================== 消息发送 ====================

@action_router.post("/send")
async def action_send(request: dict):
    """
    发送文本消息

    Body:
        - token:     鉴权 token
        - receiver:  接收者（chat_id 或群名）
        - is_master: 为 true 时发给管理员，不需要 receiver（可选）
        - content:   消息内容
        - at_user:   要@的用户（可选）
        - platform:  目标平台 "wechat"(默认) | "lark"
    """
    verify_token(request.get("token", ""))

    receiver = request.get("receiver", "")
    is_master = bool(request.get("is_master"))
    content = request.get("content", "")
    at_user = request.get("at_user", "")
    platform = request.get("platform", "wechat")

    if not content or not (receiver or is_master):
        raise ValidationException("receiver 和 content 不能为空")

    if is_master:
        LOG.info("action/send: to master, content=%s", content[:50])
        success, error_msg = await base_client.send_to_master(content)
    else:
        LOG.info("action/send: platform=%s receiver=%s at_user=%s content=%s",
                 platform, receiver, at_user, content[:50])
        success, error_msg = await base_client.send_text(receiver, at_user, content, platform=platform)

    if not success:
        raise ValidationException(f"消息发送失败: {error_msg}")

    return ApiResponse(data=None)


@action_router.post("/send-file")
async def action_send_file(request: dict):
    """
    发送文件/图片/视频

    Body:
        - token:    鉴权 token
        - receiver: 接收者
        - platform: 目标平台 "wechat"(默认) | "lark"
        - path:     AI 生成文件的相对路径，如 gen_img/abc.jpg、gen_video/abc.mp4
                    Server 拼成 AI 的 /media URL 交给 base，base 自己下载后发送。
    """
    verify_token(request.get("token", ""))

    receiver = request.get("receiver", "")
    platform = request.get("platform", "wechat")
    path = request.get("path", "")

    if not receiver:
        raise ValidationException("receiver 不能为空")
    if not path:
        raise ValidationException("path 不能为空")

    url = to_url(path, ai_host())
    LOG.info("action/send-file: platform=%s receiver=%s url=%s", platform, receiver, url)

    success, error_msg = await base_client.send_file(url, receiver, platform=platform)

    if not success:
        raise ValidationException(f"文件发送失败: {error_msg}")

    return ApiResponse(data=None)


# ==================== 提醒管理 ====================

@action_router.post("/reminder/add")
async def action_add_reminder(request: dict):
    verify_token(request.get("token", ""))
    job_id = request.get("job_id", "")
    target_time_iso = request.get("target_time_iso", "")
    receiver = request.get("receiver", "")
    at_user = request.get("at_user", "")
    platform = request.get("platform", "wechat")
    content = request.get("content", "")
    if not job_id or not target_time_iso or not receiver or not content:
        raise ValidationException("job_id、target_time_iso、receiver、content 均不能为空")
    try:
        data = reminder_service.add_reminder(job_id, target_time_iso, receiver, content, at_user, platform)
    except ValueError as e:
        raise ValidationException(str(e))
    return ApiResponse(data=data)


@action_router.post("/reminder/delete")
async def action_delete_reminder(request: dict):
    verify_token(request.get("token", ""))
    job_id = request.get("job_id", "")
    if not job_id:
        raise ValidationException("job_id 不能为空")
    try:
        reminder_service.delete_reminder(job_id)
    except ValueError as e:
        raise ValidationException(str(e))
    return ApiResponse(data=None)


@action_router.post("/reminder/query")
async def action_query_reminder(request: dict):
    verify_token(request.get("token", ""))
    receiver = request.get("receiver", "")
    platform = request.get("platform", "wechat")
    result = reminder_service.query_reminders(receiver, platform)
    LOG.info("action/reminder/query: platform=%s receiver=%s count=%d", platform, receiver, len(result))
    return ApiResponse(data={"jobs": result})


@action_router.post("/reminder/update")
async def action_update_reminder(request: dict):
    verify_token(request.get("token", ""))
    job_id = request.get("job_id", "").strip()
    new_time_iso = request.get("new_time_iso", "").strip()
    new_content = request.get("new_content", "").strip()
    if not job_id:
        raise ValidationException("job_id 不能为空")
    try:
        data = reminder_service.update_reminder(job_id, new_time_iso, new_content)
    except ValueError as e:
        raise ValidationException(str(e))
    return ApiResponse(data=data)


# ==================== 监听管理 ====================

@action_router.post("/listen/add")
async def action_listen_add(request: dict):
    """
    添加 WeChat 监听

    Body:
        - token: 鉴权 token
        - chat_name: 聊天对象名称
    """
    verify_token(request.get("token", ""))

    chat_name = request.get("chat_name", "")
    if not chat_name:
        raise ValidationException("chat_name 不能为空")

    result = await listen_manager.add_listen(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "添加监听失败"))

    LOG.info("action/listen/add: chat_name=%s", chat_name)
    return ApiResponse(data=result)


@action_router.post("/listen/remove")
async def action_listen_remove(request: dict):
    """
    移除 WeChat 监听

    Body:
        - token: 鉴权 token
        - chat_name: 聊天对象名称
    """
    verify_token(request.get("token", ""))

    chat_name = request.get("chat_name", "")
    if not chat_name:
        raise ValidationException("chat_name 不能为空")

    result = await listen_manager.remove_listen(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "移除监听失败"))

    LOG.info("action/listen/remove: chat_name=%s", chat_name)
    return ApiResponse(data=result)
