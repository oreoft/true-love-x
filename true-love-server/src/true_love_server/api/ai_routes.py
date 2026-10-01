# -*- coding: utf-8 -*-
"""
AI Routes - 给 AI 调的业务接口（/action/*）

AI 处理完消息后通过这些接口回到 server：发消息、管理提醒和监听、查聊天记录、手动跑定时任务。
所有接口都要 token，请求体里的 bot_id 是这次处理的消息所属的机器人；不传时用默认机器人
（比如 AI 启动时通知管理员）。server 按 bot_id 查出它的 base 和库，AI 不用关心平台。
"""

import logging

from fastapi import APIRouter
from true_love_common.hosts import ai_host
from true_love_common.media import to_url

from . import deps
from .deps import verify_token
from .exception_handlers import ApiResponse, ValidationException
from ..core.db_engine import bot_session
from ..services import base_client, reminder_service, task_service
from ..services.bot_registry import BotRecord
from ..services.group_message_repository import GroupMessageRepository
from ..services.listen_manager import get_listen_manager

LOG = logging.getLogger("AiRoutes")

ai_router = APIRouter(prefix="/action")


def _bot(request: dict) -> BotRecord:
    """校验 token，返回请求所属的机器人"""
    verify_token(request.get("token", ""))
    return deps.bot(request.get("bot_id"))


# ==================== 消息发送 ====================

@ai_router.post("/send")
async def action_send(request: dict):
    """
    发送文本消息

    Body:
        - receiver:  接收者（chat_id 或群名）
        - is_master: 为 true 时发给这个机器人的管理员，不需要 receiver（可选）
        - content:   消息内容
        - at_user:   要@的用户（可选）
        - quote_msg_id: 要引用回复的消息 id（可选），base 引用不了时照常发送
    """
    bot = _bot(request)
    receiver = request.get("receiver", "")
    is_master = bool(request.get("is_master"))
    content = request.get("content", "")
    at_user = request.get("at_user", "")

    if not content or not (receiver or is_master):
        raise ValidationException("receiver 和 content 不能为空")

    if is_master:
        LOG.info("action/send: bot_id=%s to master, content=%s", bot.bot_id, content[:50])
        success, error_msg = await base_client.send_to_master(bot.bot_id, content)
    else:
        LOG.info("action/send: bot_id=%s receiver=%s at_user=%s content=%s",
                 bot.bot_id, receiver, at_user, content[:50])
        success, error_msg = await base_client.send_text(bot.bot_id, receiver, at_user, content,
                                                         quote_msg_id=request.get("quote_msg_id", ""))

    if not success:
        raise ValidationException(f"消息发送失败: {error_msg}")
    return ApiResponse(data=None)


@ai_router.post("/send-file")
async def action_send_file(request: dict):
    """
    发送 AI 生成的文件/图片/视频

    Body:
        - receiver: 接收者
        - path:     AI 生成文件的相对路径，如 gen_img/abc.jpg、gen_video/abc.mp4；
                    server 拼成 AI 的 /media URL 交给 base，base 自己下载后发送
    """
    bot = _bot(request)
    receiver = request.get("receiver", "")
    path = request.get("path", "")
    if not receiver:
        raise ValidationException("receiver 不能为空")
    if not path:
        raise ValidationException("path 不能为空")

    url = to_url(path, ai_host())
    LOG.info("action/send-file: bot_id=%s receiver=%s url=%s", bot.bot_id, receiver, url)
    success, error_msg = await base_client.send_file(bot.bot_id, url, receiver)
    if not success:
        raise ValidationException(f"文件发送失败: {error_msg}")
    return ApiResponse(data=None)


# ==================== 聊天记录 ====================

@ai_router.post("/history")
async def action_history(request: dict):
    """
    查询机器人库里的聊天记录（群分析、发言分析、群上下文用）

    Body:
        - chat_id:     群聊 ID（必填）
        - sender_id:   发送者唯一 ID（可选，与 sender_name 互斥，优先级更高）
        - sender_name: 发送者昵称（可选，同名时返回所有匹配，仅在无法获取 ID 时使用）
        - limit:       最大返回条数（默认 100）
        - tail_id:     游标 ID，仅返回 id < tail_id 的消息（可选，用于向前翻页）
    """
    bot = _bot(request)
    chat_id = request.get("chat_id", "")
    if not chat_id:
        raise ValidationException("chat_id 不能为空")
    sender_id = request.get("sender_id") or None
    sender_name = request.get("sender_name") or None
    limit = int(request.get("limit", 100))
    tail_id = request.get("tail_id")
    tail_id = int(tail_id) if tail_id is not None else None

    with bot_session(bot.bot_id) as db:
        messages = GroupMessageRepository(db).get_messages(chat_id, sender_id, sender_name, limit, tail_id)

    LOG.info("action/history: bot_id=%s chat_id=%s sender_id=%s sender_name=%s tail_id=%s count=%d",
             bot.bot_id, chat_id, sender_id, sender_name, tail_id, len(messages))
    return ApiResponse(data={"messages": messages})


# ==================== 提醒管理 ====================

def _reminder_call(func, *args):
    try:
        return func(*args)
    except ValueError as e:
        raise ValidationException(str(e))


@ai_router.post("/reminder/add")
async def action_add_reminder(request: dict):
    bot = _bot(request)
    job_id = request.get("job_id", "")
    target_time_iso = request.get("target_time_iso", "")
    receiver = request.get("receiver", "")
    content = request.get("content", "")
    if not job_id or not target_time_iso or not receiver or not content:
        raise ValidationException("job_id、target_time_iso、receiver、content 均不能为空")
    data = _reminder_call(reminder_service.add_reminder, bot.bot_id, job_id, target_time_iso, receiver,
                          content, request.get("at_user", ""))
    return ApiResponse(data=data)


@ai_router.post("/reminder/delete")
async def action_delete_reminder(request: dict):
    bot = _bot(request)
    job_id = request.get("job_id", "")
    if not job_id:
        raise ValidationException("job_id 不能为空")
    _reminder_call(reminder_service.delete_reminder, bot.bot_id, job_id)
    return ApiResponse(data=None)


@ai_router.post("/reminder/query")
async def action_query_reminder(request: dict):
    bot = _bot(request)
    receiver = request.get("receiver", "")
    result = reminder_service.query_reminders(bot.bot_id, receiver)
    LOG.info("action/reminder/query: bot_id=%s receiver=%s count=%d", bot.bot_id, receiver, len(result))
    return ApiResponse(data={"jobs": result})


@ai_router.post("/reminder/update")
async def action_update_reminder(request: dict):
    bot = _bot(request)
    job_id = request.get("job_id", "").strip()
    if not job_id:
        raise ValidationException("job_id 不能为空")
    data = _reminder_call(reminder_service.update_reminder, bot.bot_id, job_id,
                          request.get("new_time_iso", "").strip(), request.get("new_content", "").strip())
    return ApiResponse(data=data)


# ==================== 监听管理（微信专属） ====================

@ai_router.post("/listen/add")
async def action_listen_add(request: dict):
    """Body: chat_name"""
    verify_token(request.get("token", ""))
    bot = deps.wechat_bot(request.get("bot_id"))
    chat_name = request.get("chat_name", "")
    if not chat_name:
        raise ValidationException("chat_name 不能为空")

    result = await get_listen_manager(bot.bot_id).add_listen(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "添加监听失败"))
    LOG.info("action/listen/add: bot_id=%s chat_name=%s", bot.bot_id, chat_name)
    return ApiResponse(data=result)


@ai_router.post("/listen/remove")
async def action_listen_remove(request: dict):
    """Body: chat_name"""
    verify_token(request.get("token", ""))
    bot = deps.wechat_bot(request.get("bot_id"))
    chat_name = request.get("chat_name", "")
    if not chat_name:
        raise ValidationException("chat_name 不能为空")

    result = await get_listen_manager(bot.bot_id).remove_listen(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "移除监听失败"))
    LOG.info("action/listen/remove: bot_id=%s chat_name=%s", bot.bot_id, chat_name)
    return ApiResponse(data=result)


# ==================== 定时任务 ====================

@ai_router.post("/job/run")
async def action_run_job(request: dict):
    """
    立即执行这个机器人在某个任务名下的所有定时任务

    Body: job_name
    """
    bot = _bot(request)
    job_name = request.get("job_name", "").strip()
    try:
        started = task_service.run_by_job_name(bot.bot_id, job_name)
    except ValueError as e:
        raise ValidationException(str(e))
    LOG.info("action/job/run: bot_id=%s job=%s tasks=%s", bot.bot_id, job_name, started)
    return ApiResponse(data={"job_name": job_name, "status": "triggered", "tasks": len(started)})
