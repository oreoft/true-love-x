# -*- coding: utf-8 -*-
"""技能权限和 AI 的平台级设置"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from true_love_ai.core.db_engine import Base


class SkillAccess(Base):
    """
    技能权限表：每个技能（内置的和安装的）一行，所有机器人共用

    points 是权限点列表（JSON），匹配上任意一个就能用。权限点统一存成四段 "平台:号:群:人"，
    每段可以是 *，如 "wechat:*:*:张三"、"wechat:<号>:<群名>:*"，写法和显示见 skill_access_service。
    """

    __tablename__ = "skill_access"

    skill = Column(String(128), primary_key=True, comment="内置技能名或安装技能的 id")
    kind = Column(String(16), nullable=False, comment="builtin（代码里的）或 installed（安装的）")
    points = Column(Text, nullable=False, comment="权限点 JSON 数组")
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class AiSetting(Base):
    """AI 的平台级设置，key-value"""

    __tablename__ = "ai_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
