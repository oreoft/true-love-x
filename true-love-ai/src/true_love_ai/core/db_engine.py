# -*- coding: utf-8 -*-
"""
AI 数据库引擎
"""

import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from . import migrate

LOG = logging.getLogger("DBEngine")

DBS_DIR = "dbs"
os.makedirs(DBS_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DBS_DIR}/ai_data.db"
_DB_PATH = f"{DBS_DIR}/ai_data.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# schema_migrations 表：记录已执行的 migration 版本，由 create_all() 统一建表
# 注意：SchemaMigration 在 models/schema_migration.py 中定义，必须在 init_db() 里 import 才能注册到 Base


def init_db():
    """初始化数据库，建表并执行 schema migration"""
    try:
        from true_love_ai.models.user_memory import UserMemory  # noqa: F401
        from true_love_ai.models.session_message import SessionMessage  # noqa: F401
        from true_love_ai.models.dynamic_skill import DynamicSkill  # noqa: F401
        from true_love_ai.models.schema_migration import SchemaMigration  # noqa: F401
        from true_love_ai.models.persona import Persona  # noqa: F401
        from true_love_ai.models.skill_access import AiSetting, SkillAccess  # noqa: F401
        from true_love_ai.models.model_setting import ModelSetting  # noqa: F401

        Base.metadata.create_all(bind=engine)

        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

        LOG.info("AI Database initialized: %s", DATABASE_URL)
        migrate.run(_DB_PATH)

    except Exception as e:
        LOG.error("AI Database init failed: %s", e)
        raise
