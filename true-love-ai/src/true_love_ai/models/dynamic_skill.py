# -*- coding: utf-8 -*-
"""动态技能模型"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Index

from true_love_ai.core.db_engine import Base


class DynamicSkill(Base):
    """动态技能表：存储用户/管理员保存的可复用 shell 命令技能"""

    __tablename__ = 'dynamic_skills'

    id = Column(String(64), primary_key=True, comment="技能唯一ID（英文下划线）")
    name = Column(String(128), nullable=False, comment="技能名称")
    description = Column(Text, nullable=False, comment="触发描述，注入进 LLM 上下文")
    command = Column(Text, nullable=False, comment="shell 命令模板，支持 {param} 占位符")
    parameters = Column(Text, nullable=True, comment="参数定义 JSON：{name: {default, desc}}")
    creator = Column(String(128), nullable=True, comment="创建者 wxid")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    usage_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('idx_dynamic_skills_creator', 'creator'),
    )

    def __repr__(self):
        return f"<DynamicSkill(id={self.id}, name={self.name})>"
