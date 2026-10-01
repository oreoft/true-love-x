# -*- coding: utf-8 -*-
"""
Bot Settings - 机器人开关

每个机器人的开关存在它自己的库里，只有 server 读写：
- private_poll：微信机器人是否靠主窗口红点轮询来收没开子窗口的私聊，base 连上微信时随监听列表来取
"""

import logging

from ..core.db_engine import bot_session
from ..models.bot_setting import BotSetting

LOG = logging.getLogger("BotSettings")

PRIVATE_POLL = "private_poll"


def get_bool(bot_id: str, key: str, default: bool = False) -> bool:
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
        return default if row is None else row.value == "true"


def set_bool(bot_id: str, key: str, value: bool) -> None:
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
        if row is None:
            db.add(BotSetting(key=key, value=_text(value)))
        else:
            row.value = _text(value)
        db.commit()
    LOG.info("Bot [%s] setting %s = %s", bot_id, key, value)


def _text(value: bool) -> str:
    return "true" if value else "false"
