# -*- coding: utf-8 -*-
"""
Message Service - base 转来的消息

所有消息存进机器人自己的库；@ 机器人或私聊的消息交给 AI。AI 没接住时由 server 直接回一句，
免得用户以为机器人假死。
"""

import asyncio
import logging

from true_love_common.chat_msg import ChatMsg
from true_love_common.media import attach_urls

from . import base_client
from .ai_client import business as ai
from .bot_registry import BotRecord
from .group_message_repository import GroupMessageRepository
from ..core.db_engine import bot_session

LOG = logging.getLogger("MessageService")

AI_UNAVAILABLE_REPLY = "啊哦~AI酱 暂时连不上，稍后再试试捏~"


async def handle_incoming(bot: BotRecord, msg: ChatMsg) -> None:
    """存储消息（best-effort）并按需触发 AI，两个逻辑互相独立"""
    # 消息属于报上来的这个机器人，不信消息体里的 bot_id
    msg.bot_id = bot.bot_id
    is_new = await asyncio.to_thread(_save_message, bot.bot_id, msg)
    if not is_new:
        LOG.warning("重复消息已过滤，跳过 AI 触发: bot_id=%s msg_hash=%s sender_id=%s",
                    bot.bot_id, msg.msg_hash, msg.sender_id)
        return

    if msg.is_at_me or not msg.is_group:
        try:
            await asyncio.to_thread(_trigger_ai, bot, msg)
        except Exception as e:
            LOG.error(f"触发 AI 失败: {e}", exc_info=True)
            await _send_ai_unavailable(bot, msg)


def _save_message(bot_id: str, msg: ChatMsg) -> bool:
    """返回是否为新消息；存储失败按新消息处理，不影响触发 AI"""
    try:
        with bot_session(bot_id) as db:
            return GroupMessageRepository(db).save(msg)
    except Exception as e:
        LOG.error(f"消息存储失败: {e}", exc_info=True)
        return True


def _trigger_ai(bot: BotRecord, msg: ChatMsg) -> None:
    """交给 AI；媒体存在发消息的 base 上，换成 base 的 URL 让 AI 直接下载"""
    attach_urls(msg, bot.callback)
    ai.trigger(msg)


async def _send_ai_unavailable(bot: BotRecord, msg: ChatMsg) -> None:
    receiver = msg.chat_id if msg.is_group else msg.sender_id
    at_user = msg.sender_id if msg.is_group else ""
    ok, err = await base_client.send_text(bot.bot_id, receiver, at_user, AI_UNAVAILABLE_REPLY)
    if not ok:
        LOG.error("AI 不可用提示发送失败: bot_id=%s receiver=%s err=%s", bot.bot_id, receiver, err)
