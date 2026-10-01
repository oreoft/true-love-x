# -*- coding: utf-8 -*-
"""
Message Service - base 转来的消息

所有消息存进机器人自己的库；@ 机器人或私聊的消息交给 AI，同一个人找得太频繁时只入库（见 ai_rate_limit）。
群里的链接、PDF、图片、笔记在后台开了对应开关时，不 @ 也交给 AI，每种有自己的疲劳限制（见 auto_ai_limit）。
AI 没接住时由 server 直接回一句，免得用户以为机器人假死。
"""

import asyncio
import logging

from true_love_common.chat_msg import ChatMsg
from true_love_common.media import attach_urls

from . import ai_rate_limit, auto_ai_limit, base_client, bot_settings
from .ai_client import business as ai
from .bot_registry import BotRecord
from .group_message_repository import GroupMessageRepository
from ..core.db_engine import bot_session

LOG = logging.getLogger("MessageService")

AI_UNAVAILABLE_REPLY = "啊哦~AI酱 暂时连不上，稍后再试试捏~"


async def handle_incoming(bot: BotRecord, msg: ChatMsg, archive_only: bool = False) -> None:
    """存储消息（best-effort）并按需触发 AI，两个逻辑互相独立；archive_only 的只存档"""
    # 消息属于报上来的这个机器人，不信消息体里的 bot_id
    msg.bot_id = bot.bot_id
    is_new = await asyncio.to_thread(_save_message, bot.bot_id, msg)
    if not is_new:
        LOG.warning("重复消息已过滤，跳过 AI 触发: bot_id=%s msg_hash=%s sender_id=%s",
                    bot.bot_id, msg.msg_hash, msg.sender_id)
        return
    if archive_only:
        LOG.info("补发的消息只存档，不交给 AI: bot_id=%s msg_hash=%s", bot.bot_id, msg.msg_hash)
        return

    # 判断要不要交给 AI、限额、限流、交给 AI 整段出错都按 AI 没接住处理，回一句免得用户以为机器人假死
    try:
        if not (msg.is_at_me or not msg.is_group or await asyncio.to_thread(_auto_ai, bot.bot_id, msg)):
            return
        limit = await asyncio.to_thread(bot_settings.get_limit, bot.bot_id, bot_settings.AI_RATE_LIMIT)
        decision = ai_rate_limit.check(bot.bot_id, msg.chat_id, msg.sender_id,
                                       limit=limit["count"], seconds=limit["seconds"])
        if decision != ai_rate_limit.ALLOW:
            LOG.warning("找 AI 太频繁，只入库: bot_id=%s chat=%s sender_id=%s", bot.bot_id, msg.chat_id, msg.sender_id)
            if decision == ai_rate_limit.NOTIFY:
                await _reply(bot, msg, ai_rate_limit.BUSY_REPLY)
            return
        await asyncio.to_thread(_trigger_ai, bot, msg)
    except Exception:
        LOG.exception("交给 AI 失败: bot_id=%s chat=%s sender_id=%s", bot.bot_id, msg.chat_id, msg.sender_id)
        await _send_ai_unavailable(bot, msg)


def _auto_ai(bot_id: str, msg: ChatMsg) -> bool:
    """
    群里没 @ 的消息，按开关和疲劳限制决定要不要交给 AI

    没内容可看的不交：取不到地址的链接、不是 PDF 或没下载下来的文件（AI 只读得了 PDF）、
    没下载下来的图片、只有图的笔记。表情包是单独的 emotion 类型，不在这里。
    """
    kind = msg.msg_type
    if kind == "link":
        has_content = bool(msg.link_msg and msg.link_msg.url)
    elif kind == "file":
        resource = msg.file_msg.resource if msg.file_msg else None
        has_content = bool(resource and resource.ref.lower().endswith(".pdf"))
    elif kind == "image":
        has_content = bool(msg.image_msg and msg.image_msg.resource)
    elif kind == "note":
        # 群里的笔记 base 不点开，文字笔记的 content 是"笔记"加正文，只有图的笔记就只有"笔记"两个字
        has_content = (msg.content or "").strip() not in ("", "笔记")
    else:
        return False
    if not has_content or not bot_settings.get_bool(bot_id, f"auto_ai_{kind}"):
        return False
    limit = bot_settings.get_limit(bot_id, f"auto_ai_{kind}_limit")
    if not auto_ai_limit.allow(bot_id, msg.chat_id, kind, limit["count"], limit["seconds"]):
        LOG.info("群里自动交给 AI 太频繁，只入库: bot_id=%s chat=%s type=%s limit=%s",
                 bot_id, msg.chat_id, kind, limit)
        return False
    return True


def _save_message(bot_id: str, msg: ChatMsg) -> bool:
    """返回是否为新消息；存储失败按新消息处理，不影响触发 AI"""
    try:
        with bot_session(bot_id) as db:
            return GroupMessageRepository(db).save(msg)
    except Exception:
        LOG.exception("消息存储失败: bot_id=%s msg_hash=%s", bot_id, msg.msg_hash)
        return True


def _trigger_ai(bot: BotRecord, msg: ChatMsg) -> None:
    """交给 AI；媒体存在发消息的 base 上，换成 base 的 URL 让 AI 直接下载"""
    attach_urls(msg, bot.callback)
    ai.trigger(msg)


async def _send_ai_unavailable(bot: BotRecord, msg: ChatMsg) -> None:
    await _reply(bot, msg, AI_UNAVAILABLE_REPLY)


async def _reply(bot: BotRecord, msg: ChatMsg, content: str) -> None:
    """由 server 直接回复这条消息：群里 @ 发送者，私聊直接回"""
    receiver = msg.chat_id if msg.is_group else msg.sender_id
    at_user = msg.sender_id if msg.is_group else ""
    ok, err = await base_client.send_text(bot.bot_id, receiver, at_user, content)
    if not ok:
        LOG.error("server 回复发送失败: bot_id=%s receiver=%s err=%s", bot.bot_id, receiver, err)
