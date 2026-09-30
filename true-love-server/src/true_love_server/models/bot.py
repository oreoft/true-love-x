# -*- coding: utf-8 -*-
"""
机器人登记表

一个机器人是一个平台上的一个账号（微信是一个 wxid），对应一个 base。base 每次调 server 都会报上自己的信息，
server 记在这张表里：往哪个地址回调它、它是哪个平台。这张表在平台库里，所有机器人共用；
每个机器人自己的数据（聊天记录、监听、提醒、定时任务）在各自的库里。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import declarative_base

PlatformBase = declarative_base()


class Bot(PlatformBase):
    __tablename__ = "bots"

    bot_id        = Column(String(64),  primary_key=True, comment="机器人标识，微信是 wxid")
    platform      = Column(String(32),  nullable=False, comment="wechat / lark ...")
    name          = Column(String(128), nullable=False, default="", comment="账号昵称，只用来显示")
    callback      = Column(String(256), nullable=False, comment="回调这个 base 的地址")
    registered_at = Column(DateTime,    nullable=False, default=datetime.now, comment="第一次登记的时间")
    last_seen_at  = Column(DateTime,    nullable=False, default=datetime.now, comment="最近一次调 server 的时间")
