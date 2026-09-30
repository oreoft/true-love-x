# -*- coding: utf-8 -*-
"""模型设置"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String

from true_love_ai.core.db_engine import Base


class ModelSetting(Base):
    """模型设置表：改过的模型，所有机器人共用；没有记录的类别用代码里的默认模型"""

    __tablename__ = "model_settings"

    category = Column(String(32), primary_key=True, comment="类别，如 chat、image")
    key = Column(String(16), primary_key=True, comment="default（主力）或 fallback（备用）")
    value = Column(String(256), nullable=False, comment="完整的 LiteLLM 模型字符串")
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
