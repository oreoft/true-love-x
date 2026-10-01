# -*- coding: utf-8 -*-
"""
Bot Settings - 机器人设置

每个机器人的设置存在它自己的库里，只有 server 读写。微信机器人的设置（wechat_settings）
在 base 连上微信时随监听列表下发，后台改了会推给 base：
- private_poll：没开子窗口的私聊靠主窗口红点轮询来收
- auto_accept_friends：自动通过好友申请
- group_reply：群回复方式，at / tickle（拍一拍）/ quote（引用）里勾一个或多个，多个时随机挑

群里不 @ 也交给 AI 的设置只有 server 用，不发给 base。每种消息一个开关、一个疲劳限制：
- auto_ai_<类型>：开关，类型见 AUTO_AI_TYPES（链接、PDF 文件、图片、笔记）
- auto_ai_<类型>_limit：{"count": 条数, "seconds": 秒数}，同一个群里这种消息每 seconds 秒最多自动交给 AI count 条
- ai_rate_limit：{"count": 次数, "seconds": 秒数}，同一个人在同一个会话里找 AI 的限频（见 ai_rate_limit）
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
AUTO_AI_TYPES = ("link", "file", "image", "note")
AUTO_AI_SWITCHES = tuple(f"auto_ai_{kind}" for kind in AUTO_AI_TYPES)
AUTO_AI_LIMITS = tuple(f"auto_ai_{kind}_limit" for kind in AUTO_AI_TYPES)
DEFAULT_AUTO_AI_LIMIT = {"count": 5, "seconds": 600}
AI_RATE_LIMIT = "ai_rate_limit"
DEFAULT_AI_RATE_LIMIT = {"count": 6, "seconds": 180}
# 后台能设的限频，和各自的默认值；都只有 server 用
LIMITS = {**{key: DEFAULT_AUTO_AI_LIMIT for key in AUTO_AI_LIMITS}, AI_RATE_LIMIT: DEFAULT_AI_RATE_LIMIT}


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


def get_limit(bot_id: str, key: str) -> dict:
    """限频设置，存的是"条数,秒数"，没设过用 LIMITS 里的默认值"""
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
    try:
        count, seconds = (int(part) for part in row.value.split(","))
        return {"count": count, "seconds": seconds}
    except (AttributeError, ValueError):
        return dict(LIMITS[key])


def admin_settings(bot_id: str) -> dict:
    """后台看到的全部设置：微信设置，加上群里自动交给 AI 的开关和各种限频"""
    return {**wechat_settings(bot_id),
            **{key: get_bool(bot_id, key) for key in AUTO_AI_SWITCHES},
            **{key: get_limit(bot_id, key) for key in LIMITS}}


def set_bool(bot_id: str, key: str, value: bool) -> None:
    _set(bot_id, key, _text(value))
    LOG.info("Bot [%s] setting %s = %s", bot_id, key, value)


def set_limit(bot_id: str, key: str, limit: dict) -> None:
    _set(bot_id, key, f"{limit['count']},{limit['seconds']}")
    LOG.info("Bot [%s] setting %s = %s", bot_id, key, limit)


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
