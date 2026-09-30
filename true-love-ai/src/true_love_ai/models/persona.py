# -*- coding: utf-8 -*-
"""人设模型"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from true_love_ai.core.db_engine import Base


class Persona(Base):
    """
    人设表：机器人回复时用的 system prompt 和语音风格

    一行管一个范围：bot_id 为 "*" 时是所有机器人共用的默认；chat 为空时是这个机器人的默认，
    否则只管这个机器人里的这个群或私聊对象（微信是群名或昵称）。
    """

    __tablename__ = "personas"

    bot_id = Column(String(128), primary_key=True, comment='机器人，"*" 表示所有机器人')
    chat = Column(String(256), primary_key=True, default="", comment="群名或私聊对象，空表示整个机器人")
    prompt = Column(Text, nullable=False, default="", comment="system prompt，{name} 换成机器人的昵称；空表示沿用上一级")
    voice_style = Column(Text, nullable=False, default="", comment="语音合成时加在正文前的风格描述；空表示沿用上一级")
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "chat": self.chat,
            "prompt": self.prompt,
            "voice_style": self.voice_style,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
