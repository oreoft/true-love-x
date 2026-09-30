# -*- coding: utf-8 -*-
"""
群消息记录模型

每个机器人一个库，库里只有这个机器人收到的消息，所以不再按平台区分。
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class GroupMessage(Base):
    """群消息记录表"""

    __tablename__ = 'group_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 消息基础信息
    msg_id = Column(String(64), nullable=False, index=True, comment="消息ID")
    msg_hash = Column(String(64), nullable=False, unique=True, comment="消息哈希，用于去重")
    msg_type = Column(String(32), nullable=False, comment="消息类型: text/image/voice/video/file/link/refer")

    # 发送者
    sender_id = Column(String(128), nullable=False, default="", index=True, comment="平台唯一发送者 ID")
    sender_name = Column(String(128), nullable=False, default="", comment="发送者显示名")

    # 会话
    chat_id = Column(String(128), nullable=False, index=True, comment="平台唯一会话 ID")
    chat_name = Column(String(128), nullable=False, default="", comment="群名/好友名（显示用）")

    # 消息内容
    content = Column(Text, nullable=False, default="", comment="消息文本内容")

    # 标识字段
    is_group = Column(Boolean, nullable=False, default=True, comment="是否群聊消息")
    is_at_me = Column(Boolean, nullable=False, default=False, comment="是否@我")

    # 媒体字段（JSON 存储）
    image_msg = Column(Text, nullable=True, comment="图片消息JSON")
    voice_msg = Column(Text, nullable=True, comment="语音消息JSON")
    video_msg = Column(Text, nullable=True, comment="视频消息JSON")
    file_msg = Column(Text, nullable=True, comment="文件消息JSON")
    link_msg = Column(Text, nullable=True, comment="链接消息JSON")
    refer_msg = Column(Text, nullable=True, comment="引用/回复消息JSON")

    # 时间戳
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="记录时间")

    __table_args__ = (
        Index('idx_chat_time', 'chat_id', 'created_at'),
    )

    def __repr__(self):
        return (f"<GroupMessage(id={self.id}, sender_id={self.sender_id}, "
                f"chat_id={self.chat_id}, msg_type={self.msg_type}, "
                f"created_at={self.created_at})>")
