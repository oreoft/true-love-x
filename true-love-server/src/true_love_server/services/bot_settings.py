# -*- coding: utf-8 -*-
"""
Bot Settings - 机器人开关

每个机器人的开关存在它自己的库里，只有 server 读写。微信机器人的功能开关（WECHAT_SWITCHES）
在 base 连上微信时随监听列表下发，后台改了会推给 base：
- private_poll：没开子窗口的私聊靠主窗口红点轮询来收
- auto_accept_friends：自动通过好友申请
"""

import logging

from ..core.db_engine import bot_session
from ..models.bot_setting import BotSetting

LOG = logging.getLogger("BotSettings")

PRIVATE_POLL = "private_poll"
AUTO_ACCEPT_FRIENDS = "auto_accept_friends"
WECHAT_SWITCHES = (PRIVATE_POLL, AUTO_ACCEPT_FRIENDS)


def get_bool(bot_id: str, key: str, default: bool = False) -> bool:
    with bot_session(bot_id) as db:
        row = db.get(BotSetting, key)
        return default if row is None else row.value == "true"


def wechat_switches(bot_id: str) -> dict[str, bool]:
    """微信机器人的全部功能开关，没设过的是关"""
    return {key: get_bool(bot_id, key) for key in WECHAT_SWITCHES}


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
