# -*- coding: utf-8 -*-
"""
Listen Store - 监听列表持久化

每个微信机器人的监听列表存在它自己的库里，只有 server 读写；
base 连上微信时通过 /base/listen/list 接口来取。
"""

import logging

from ..core.db_engine import bot_session
from ..models.listen_chat import ListenChat

LOG = logging.getLogger("ListenStore")


def list_all(bot_id: str) -> list[str]:
    """全部监听对象，按加入顺序"""
    with bot_session(bot_id) as db:
        rows = db.query(ListenChat.chat_name).order_by(ListenChat.created_at).all()
        return [row.chat_name for row in rows]


def exists(bot_id: str, chat_name: str) -> bool:
    with bot_session(bot_id) as db:
        return db.get(ListenChat, chat_name) is not None


def add(bot_id: str, chat_name: str) -> bool:
    """加入监听列表，已存在时返回 False"""
    with bot_session(bot_id) as db:
        if db.get(ListenChat, chat_name) is not None:
            return False
        db.add(ListenChat(chat_name=chat_name))
        db.commit()
    LOG.info("Added [%s] to the listen list of bot [%s]", chat_name, bot_id)
    return True


def remove(bot_id: str, chat_name: str) -> bool:
    """移出监听列表，不存在时返回 False"""
    with bot_session(bot_id) as db:
        row = db.get(ListenChat, chat_name)
        if row is None:
            return False
        db.delete(row)
        db.commit()
    LOG.info("Removed [%s] from the listen list of bot [%s]", chat_name, bot_id)
    return True
