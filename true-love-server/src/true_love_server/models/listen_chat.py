# -*- coding: utf-8 -*-
"""
ListenChat 模型

这个微信机器人要监听的群和好友，一行一个，由 create_all() 统一建表。
"""

from datetime import datetime

from sqlalchemy import Column, String, DateTime

from .group_message import Base


class ListenChat(Base):
    """监听列表"""

    __tablename__ = "listen_chats"

    chat_name  = Column(String(128), primary_key=True, comment="群名或好友昵称")
    created_at = Column(DateTime,    nullable=False, default=datetime.now, comment="加入时间")
