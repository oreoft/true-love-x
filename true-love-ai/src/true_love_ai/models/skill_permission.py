# -*- coding: utf-8 -*-
"""技能权限模型"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from true_love_ai.core.db_engine import Base


class SkillPermission(Base):
    """
    技能权限表：哪些人能用某个技能

    bot_id 为 "*" 时对所有机器人生效，机器人自己的规则优先。
    users 是 JSON 数组，格式同技能代码里声明的权限：["*"] / ["wechat:*"] / ["wechat:昵称", "lark:*"]。
    """

    __tablename__ = "skill_permissions"

    bot_id = Column(String(128), primary_key=True, comment='机器人，"*" 表示所有机器人')
    skill = Column(String(128), primary_key=True, comment="技能名")
    users = Column(Text, nullable=False, comment="允许使用的人，JSON 数组")
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
