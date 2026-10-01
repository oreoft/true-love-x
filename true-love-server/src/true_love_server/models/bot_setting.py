# -*- coding: utf-8 -*-
"""
BotSetting 模型

这个机器人自己的开关，一行一个键值，由 create_all() 统一建表。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String

from .group_message import Base


class BotSetting(Base):
    """机器人设置"""

    __tablename__ = "bot_settings"

    key        = Column(String(64),  primary_key=True, comment="设置名")
    value      = Column(String(256), nullable=False, comment="设置值，布尔值存 true / false")
    updated_at = Column(DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now, comment="修改时间")
