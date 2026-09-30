# -*- coding: utf-8 -*-
"""
Bot Registry - 机器人登记

base 每次调 server 都报上自己的 bot_id、平台、昵称和回调地址，这里负责登记：
第一次见到的机器人会建好它的库和提醒、定时任务的存储；回调地址变了就更新。
其他模块按 bot_id 查机器人往哪里回调、是哪个平台、能用哪些渠道专属功能。
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from true_love_common.bot import BotInfo

from ..core import Config, db_engine
from ..models.bot import Bot

LOG = logging.getLogger("BotRegistry")


# 渠道专属功能：后台按它显示菜单，接口按它拒绝别的平台的机器人
CAPABILITIES: dict[str, list[str]] = {
    "wechat": ["listen"],
}

# 机器人第一次登记、库建好以后要做的事（如给调度器挂上它的任务存储），由各模块在启动时注册
_on_new_bot: list[Callable[[str], None]] = []
_opened: set[str] = set()
_lock = threading.Lock()


class UnknownBot(LookupError):
    """没登记过的机器人"""


@dataclass(frozen=True)
class BotRecord:
    bot_id: str
    platform: str
    name: str
    callback: str
    registered_at: datetime
    last_seen_at: datetime

    @property
    def capabilities(self) -> list[str]:
        return list(CAPABILITIES.get(self.platform, []))

    def can(self, capability: str) -> bool:
        return capability in CAPABILITIES.get(self.platform, [])

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "platform": self.platform,
            "name": self.name,
            "callback": self.callback,
            "capabilities": self.capabilities,
            "registered_at": self.registered_at.isoformat(timespec="seconds"),
            "last_seen_at": self.last_seen_at.isoformat(timespec="seconds"),
        }


def _record(row: Bot) -> BotRecord:
    return BotRecord(row.bot_id, row.platform, row.name, row.callback, row.registered_at, row.last_seen_at)


def on_new_bot(callback: Callable[[str], None]) -> None:
    """机器人的库打开以后调用 callback(bot_id)；已经登记的机器人在 open_all() 时也会调用"""
    _on_new_bot.append(callback)


def _open(bot_id: str) -> None:
    """打开机器人的库，每个机器人只做一次"""
    if bot_id in _opened:
        return
    db_engine.init_bot_db(bot_id)
    for callback in _on_new_bot:
        callback(bot_id)
    _opened.add(bot_id)


def open_all() -> list[str]:
    """启动时打开所有登记过的机器人的库"""
    bot_ids = [record.bot_id for record in list_all()]
    with _lock:
        for bot_id in bot_ids:
            _open(bot_id)
    LOG.info("Opened %d bots: %s", len(bot_ids), bot_ids)
    return bot_ids


def register(info: BotInfo) -> BotRecord:
    """
    登记 base 报上来的机器人，返回登记后的记录

    Raises:
        ValueError: bot_id 不合法，或者同一个 bot_id 报了另一个平台
    """
    db_engine.check_bot_id(info.bot_id)
    now = datetime.now()
    with _lock:
        with db_engine.platform_session() as db:
            row = db.get(Bot, info.bot_id)
            is_new = row is None
            if is_new:
                row = Bot(bot_id=info.bot_id, platform=info.platform, registered_at=now)
                db.add(row)
            elif row.platform != info.platform:
                raise ValueError(f"机器人 {info.bot_id} 登记的平台是 {row.platform}，不能改成 {info.platform}")
            if row.callback != info.callback and not is_new:
                LOG.info("Bot [%s] callback changed: %s -> %s", info.bot_id, row.callback, info.callback)
            row.callback = info.callback
            row.name = info.name or row.name or ""
            row.last_seen_at = now
            db.commit()
            record = _record(row)
        if is_new:
            LOG.info("New bot registered: %s", record.to_dict())
        _open(info.bot_id)
    return record


def get(bot_id: str) -> BotRecord:
    """Raises: UnknownBot"""
    with db_engine.platform_session() as db:
        row = db.get(Bot, bot_id)
        if row is None:
            raise UnknownBot(f"未登记的机器人: {bot_id}")
        return _record(row)


def default_bot_id() -> str:
    """调用方没指定机器人时用的号：server 配置里的 default_bot_id，和合并前只有真爱粉时的行为一致"""
    return Config().DEFAULT_BOT_ID


def resolve(bot_id: Optional[str]) -> BotRecord:
    """调用方给的机器人，没给时用默认机器人。Raises: UnknownBot"""
    bot_id = bot_id or default_bot_id()
    if not bot_id:
        raise UnknownBot("没有指定机器人，server 配置里也没有 default_bot_id")
    return get(bot_id)


def reset() -> None:
    """忘掉打开过的机器人，测试换数据目录时用"""
    with _lock:
        _opened.clear()


def list_all() -> list[BotRecord]:
    """所有登记过的机器人，按登记顺序"""
    with db_engine.platform_session() as db:
        return [_record(row) for row in db.query(Bot).order_by(Bot.registered_at).all()]
