# -*- coding: utf-8 -*-
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Index

from true_love_ai.core.db_engine import Base


class SessionMessage(Base):
    """对话消息持久化表（type: 'msg' | 'tools' | 'summary'，见 session_repository）"""

    __tablename__ = 'session_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(256), nullable=False)
    type = Column(String(16), nullable=False)   # 'msg' | 'tools' | 'summary'
    role = Column(String(16), nullable=True)    # 'user' | 'assistant'，tools、summary 行为 NULL
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        Index('idx_session_messages_lookup', 'session_id', 'type'),
    )
