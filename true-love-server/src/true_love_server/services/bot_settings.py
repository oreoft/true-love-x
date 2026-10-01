# -*- coding: utf-8 -*-
"""
Bot Settings - 机器人设置

每个机器人的设置存在它自己的库里，只有 server 读写。微信机器人的设置（wechat_settings）
在 base 连上微信时随监听列表下发，后台改了会推给 base：
- private_poll：没开子窗口的私聊靠主窗口红点轮询来收
- auto_accept_friends：自动通过好友申请
- group_reply：群回复方式，at / tickle（拍一拍）/ quote（引用）里勾一个或多个，多个时随机挑

群里不 @ 也交给 AI 的开关（AUTO_AI_SWITCHES）只有 server 用，不发给 base：
- auto_ai_link：链接（公众号、小红书等）
- auto_ai_file：文件
- auto_ai_note：笔记
"""

import logging

from ..core.db_engine import bot_session
from ..models.bot_setting import BotSetting

LOG = logging.getLogger("BotSettings")

PRIVATE_POLL = "private_poll"
AUTO_ACCEPT_FRIENDS = "auto_accept_friends"
WECHAT_SWITCHES = (PRIVATE_POLL, AUTO_ACCEPT_FRIENDS)
GROUP_REPLY = "group_reply"
REPLY_STYLES = ("at", "tickle", "quote")
AUTO_AI_LINK = "auto_ai_link"
AUTO_AI_FILE = "auto_ai_file"
AUTO_AI_NOTE = "auto_ai_note"
AUTO_AI_SWITCHES = (AUTO_AI_LINK, AUTO_AI_FILE, AUTO_AI_NOTE)


def get_bool(bot_id: str, key: str, default: bool = False) -> bool:
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
        return default if row is None else row.value == "true"


def get_list(bot_id: str, key: str, default: list[str]) -> list[str]:
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
        return list(default) if row is None or not row.value else row.value.split(",")


def wechat_settings(bot_id: str) -> dict:
    """微信机器人的全部设置：开关没设过的是关，群回复方式没设过的是 @"""
    return {**{key: get_bool(bot_id, key) for key in WECHAT_SWITCHES},
            GROUP_REPLY: get_list(bot_id, GROUP_REPLY, ["at"])}


def admin_settings(bot_id: str) -> dict:
    """后台看到的全部设置：微信设置加上群里自动交给 AI 的开关"""
    return {**wechat_settings(bot_id), **{key: get_bool(bot_id, key) for key in AUTO_AI_SWITCHES}}


def set_bool(bot_id: str, key: str, value: bool) -> None:
    _set(bot_id, key, _text(value))
    LOG.info("Bot [%s] setting %s = %s", bot_id, key, value)


def set_list(bot_id: str, key: str, values: list[str]) -> None:
    _set(bot_id, key, ",".join(values))
    LOG.info("Bot [%s] setting %s = %s", bot_id, key, values)


def _set(bot_id: str, key: str, text: str) -> None:
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
        if row is None:
            db.add(BotSetting(key=key, value=text))
        else:
            row.value = text
        db.commit()


def _text(value: bool) -> str:
    return "true" if value else "false"
