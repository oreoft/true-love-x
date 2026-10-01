# -*- coding: utf-8 -*-
"""
Admin Bot Routes - tl-admin 里按机器人管理的接口（/admin/bots/*）

总览列出所有登记过的机器人；其余接口都挂在 /admin/bots/{bot_id}/ 下面，只操作这个机器人的数据：
聊天记录、提醒、定时任务（所有平台都有），监听（只有微信机器人有，看 capabilities）。
平台级的管理接口（日志）在 admin_platform_routes，存在 AI 那边的设置（人设，还有平台级的技能、模型）在 admin_ai_routes。
tl-admin 只在内网暴露，这些接口不校验 token。
"""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Body

from . import deps
from .exception_handlers import ApiResponse, ValidationException
from ..core.db_engine import bot_session
from ..services import base_client, bot_registry, bot_settings, listen_store, reminder_service, task_service
from ..services.bot_registry import BotRecord
from ..services.group_message_repository import GroupMessageRepository
from ..services.listen_manager import get_listen_manager

LOG = logging.getLogger("AdminBotRoutes")

admin_bot_router = APIRouter(prefix="/admin/bots")

MESSAGE_PAGE_MAX = 200


# ==================== 总览 ====================

async def _overview(bot: BotRecord) -> dict:
    """机器人卡片：登记信息、base 是否在线、今天的消息数、监听数、下一个提醒或任务"""
    status = await base_client.for_bot(bot.bot_id).status()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with bot_session(bot.bot_id) as db:
        today_messages = GroupMessageRepository(db).count_since(today)
    upcoming = [item["next_run_time"] for item in reminder_service.list_all_reminders(bot.bot_id)]
    upcoming += [task["next_run_time"] for task in task_service.list_tasks(bot.bot_id) if task["next_run_time"]]
    return {
        **bot.to_dict(),
        "is_default": bot.bot_id == bot_registry.default_bot_id(),
        "status": status,
        "today_messages": today_messages,
        "listen_count": len(listen_store.list_all(bot.bot_id)) if bot.can("listen") else None,
        "next_run_time": min(upcoming) if upcoming else None,
    }


@admin_bot_router.get("")
async def list_bots():
    """所有登记过的机器人，按登记顺序；在线状态是现场问 base 的"""
    bots = bot_registry.list_all()
    cards = await asyncio.gather(*(_overview(bot) for bot in bots))
    return ApiResponse(data={"bots": list(cards), "default_bot_id": bot_registry.default_bot_id()})


@admin_bot_router.get("/{bot_id}")
async def get_bot(bot_id: str):
    return ApiResponse(data=await _overview(deps.bot(bot_id)))


# ==================== 聊天记录 ====================

@admin_bot_router.get("/{bot_id}/chats")
async def list_chats(bot_id: str):
    """库里出现过的会话，最近有消息的在前"""
    bot = deps.bot(bot_id)
    with bot_session(bot.bot_id) as db:
        chats = GroupMessageRepository(db).list_chats()
    return ApiResponse(data={"chats": chats})


@admin_bot_router.get("/{bot_id}/messages")
async def list_messages(bot_id: str, chat_id: str, tail_id: int = None, keyword: str = "", limit: int = 40):
    """
    一个会话的聊天记录，按 id 往前翻页

    Query:
        - chat_id: 会话
        - tail_id: 只取 id 小于它的，不传从最新的开始
        - keyword: 只看内容里包含它的
        - limit:   每页条数，最多 200
    """
    bot = deps.bot(bot_id)
    limit = max(1, min(limit, MESSAGE_PAGE_MAX))
    with bot_session(bot.bot_id) as db:
        messages = GroupMessageRepository(db).get_messages(chat_id, limit=limit, tail_id=tail_id,
                                                           keyword=keyword.strip() or None)
    return ApiResponse(data={
        "messages": messages,
        "next_tail_id": messages[0]["id"] if len(messages) >= limit else None,
    })


@admin_bot_router.get("/{bot_id}/receivers")
async def list_receivers(bot_id: str):
    """新增提醒、定时任务时的接收者候选：监听列表加上聊过的会话"""
    bot = deps.bot(bot_id)
    names = listen_store.list_all(bot.bot_id) if bot.can("listen") else []
    with bot_session(bot.bot_id) as db:
        names += [chat["chat_id"] for chat in GroupMessageRepository(db).list_chats()]
    return ApiResponse(data={"receivers": list(dict.fromkeys(names))})


# ==================== 监听（微信专属） ====================

def _listen(bot_id: str):
    return get_listen_manager(deps.wechat_bot(bot_id).bot_id)


def _chat_name(request: dict) -> str:
    chat_name = request.get("chat_name", "")
    if not chat_name:
        raise ValidationException("chat_name 不能为空哦~")
    return chat_name


@admin_bot_router.get("/{bot_id}/listen/status")
async def listen_status(bot_id: str):
    """
    每个监听的健康状态：子窗口存在且 ChatInfo 能响应是 healthy，否则 unhealthy

    Returns:
        - listeners: 每个监听的状态列表
        - summary: {"healthy": N, "unhealthy": M}
    """
    return ApiResponse(data=await _listen(bot_id).get_listener_status())


@admin_bot_router.post("/{bot_id}/listen/add")
async def listen_add(bot_id: str, request: dict):
    result = await _listen(bot_id).add_listen(_chat_name(request))
    if not result.get("success"):
        raise ValidationException(result.get("message", "添加监听失败"))
    return ApiResponse(data=result)


@admin_bot_router.post("/{bot_id}/listen/remove")
async def listen_remove(bot_id: str, request: dict):
    result = await _listen(bot_id).remove_listen(_chat_name(request))
    if not result.get("success"):
        raise ValidationException(result.get("message", "移除监听失败"))
    return ApiResponse(data=result)


@admin_bot_router.post("/{bot_id}/listen/refresh")
async def listen_refresh(bot_id: str, request: dict = Body(default={})):
    """健康的跳过，不健康的重置"""
    result = await _listen(bot_id).refresh_listen()
    if result.get("fail_count", 0) > 0:
        raise ValidationException(f"刷新部分失败: {result.get('fail_count')} 个监听恢复失败")
    return ApiResponse(data=result)


@admin_bot_router.post("/{bot_id}/listen/reset")
async def listen_reset(bot_id: str, request: dict):
    """关闭子窗口、移除监听、重新添加，恢复一个异常的监听"""
    result = await _listen(bot_id).reset_listener(_chat_name(request))
    if not result.get("success"):
        raise ValidationException(result.get("message", "重置监听失败"))
    return ApiResponse(data=result)


@admin_bot_router.post("/{bot_id}/listen/reset-all")
async def listen_reset_all(bot_id: str, request: dict = Body(default={})):
    """关闭所有子窗口、刷新界面、逐个重新添加"""
    result = await _listen(bot_id).reset_all_listeners()
    if not result.get("success"):
        raise ValidationException(result.get("message", "重置所有监听失败"))
    return ApiResponse(data=result)


@admin_bot_router.get("/{bot_id}/listen/settings")
async def listen_settings(bot_id: str):
    """微信设置：private_poll 私聊轮询，auto_accept_friends 自动通过好友申请，group_reply 群回复方式"""
    return ApiResponse(data=bot_settings.wechat_settings(deps.wechat_bot(bot_id).bot_id))


@admin_bot_router.post("/{bot_id}/listen/settings")
async def listen_save_settings(bot_id: str, request: dict):
    """
    改微信设置：先存下来，再通知 base 立即生效；base 离线时等它下次连上微信再取

    Body:
        - 要改的设置，如 {"private_poll": true} 或 {"group_reply": ["at", "quote"]}

    Returns:
        - settings: 存下来的全部设置
        - applied: base 是否已经生效
    """
    bot = deps.wechat_bot(bot_id)
    _check_settings(request)
    for key, value in request.items():
        if key == bot_settings.GROUP_REPLY:
            bot_settings.set_list(bot.bot_id, key, value)
        else:
            bot_settings.set_bool(bot.bot_id, key, value)
    result = await base_client.wechat(bot.bot_id).apply_settings(request)
    return ApiResponse(data={"settings": bot_settings.wechat_settings(bot.bot_id),
                             "applied": result.get("success", False)})


def _check_settings(request: dict) -> None:
    if not request:
        raise ValidationException("没有要改的设置")
    for key, value in request.items():
        if key in bot_settings.WECHAT_SWITCHES:
            if not isinstance(value, bool):
                raise ValidationException(f"{key} 要是 true 或 false")
        elif key == bot_settings.GROUP_REPLY:
            if not (isinstance(value, list) and value and all(style in bot_settings.REPLY_STYLES for style in value)):
                raise ValidationException(f"群回复方式至少勾一个，只能是 {', '.join(bot_settings.REPLY_STYLES)}")
        else:
            raise ValidationException(f"不认识的设置：{key}")


@admin_bot_router.post("/{bot_id}/listen/mute-all-groups")
async def listen_mute_all_groups(bot_id: str, request: dict = Body(default={})):
    """
    把机器人微信里的群都设成消息免打扰，私聊轮询就不会点开它们；开了子窗口的群照常收消息

    Returns:
        - total / muted / already / failed，见 base 的 /groups/mute-all
    """
    result = await base_client.wechat(deps.wechat_bot(bot_id).bot_id).mute_all_groups()
    if not result.get("success"):
        raise ValidationException(result.get("message") or "设置群免打扰失败")
    return ApiResponse(data=result.get("data"))


@admin_bot_router.post("/{bot_id}/listen/get-all-message")
async def listen_get_all_message(bot_id: str, request: dict):
    """测活：取聊天窗口里的所有消息"""
    result = await _listen(bot_id).base.execute_chat(_chat_name(request), "GetAllMessage", {})
    if not result.get("success"):
        raise ValidationException(result.get("message", "获取消息失败"))
    return ApiResponse(data=result)


# ==================== 提醒 ====================

def _call(func, *args):
    try:
        return func(*args)
    except ValueError as e:
        raise ValidationException(str(e))


@admin_bot_router.get("/{bot_id}/reminders")
async def list_reminders(bot_id: str):
    bot = deps.bot(bot_id)
    result = reminder_service.list_all_reminders(bot.bot_id)
    return ApiResponse(data={"jobs": result, "total": len(result)})


@admin_bot_router.post("/{bot_id}/reminders/add")
async def add_reminder(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    receiver = request.get("receiver", "").strip()
    content = request.get("content", "").strip()
    target_time_iso = request.get("target_time_iso", "").strip()
    if not receiver or not content or not target_time_iso:
        raise ValidationException("receiver、content、target_time_iso 不能为空")
    job_id = reminder_service.make_job_id(receiver)
    data = _call(reminder_service.add_reminder, bot.bot_id, job_id, target_time_iso, receiver, content,
                 request.get("at_user", "").strip())
    LOG.info("admin reminder add: bot_id=%s job_id=%s", bot.bot_id, job_id)
    return ApiResponse(data=data)


@admin_bot_router.post("/{bot_id}/reminders/update")
async def update_reminder(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    job_id = request.get("job_id", "").strip()
    receiver = request.get("receiver", "").strip()
    content = request.get("content", "").strip()
    target_time_iso = request.get("target_time_iso", "").strip()
    if not job_id or not receiver or not content or not target_time_iso:
        raise ValidationException("job_id、receiver、content、target_time_iso 不能为空")
    data = _call(reminder_service.edit_reminder, bot.bot_id, job_id, receiver, content, target_time_iso,
                 request.get("at_user", "").strip())
    return ApiResponse(data=data)


@admin_bot_router.post("/{bot_id}/reminders/delete")
async def delete_reminder(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    job_id = request.get("job_id", "").strip()
    if not job_id:
        raise ValidationException("job_id 不能为空")
    return ApiResponse(data=_call(reminder_service.delete_reminder, bot.bot_id, job_id))


# ==================== 定时任务 ====================

@admin_bot_router.get("/{bot_id}/tasks")
async def list_tasks(bot_id: str):
    bot = deps.bot(bot_id)
    return ApiResponse(data={"tasks": task_service.list_tasks(bot.bot_id)})


@admin_bot_router.post("/{bot_id}/tasks/add")
async def add_task(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    return ApiResponse(data=_call(task_service.add_task, bot.bot_id, request.get("job_name", ""),
                                  request.get("receivers"), request.get("schedule")))


@admin_bot_router.post("/{bot_id}/tasks/update")
async def update_task(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    return ApiResponse(data=_call(task_service.update_task, bot.bot_id, request.get("task_id", ""),
                                  request.get("job_name", ""), request.get("receivers"), request.get("schedule")))


@admin_bot_router.post("/{bot_id}/tasks/delete")
async def delete_task(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    return ApiResponse(data=_call(task_service.delete_task, bot.bot_id, request.get("task_id", "")))


@admin_bot_router.post("/{bot_id}/tasks/run")
async def run_task(bot_id: str, request: dict):
    bot = deps.bot(bot_id)
    return ApiResponse(data=_call(task_service.run_now, bot.bot_id, request.get("task_id", "")))
