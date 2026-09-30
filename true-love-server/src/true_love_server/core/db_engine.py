# -*- coding: utf-8 -*-
"""
Database Engine - 数据库引擎
"""

import logging
import os
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from . import migrate
from ..models.group_message import Base
from ..models.schema_migration import SchemaMigration  # noqa: F401 — 确保 create_all() 能建表
from ..models.listen_chat import ListenChat  # noqa: F401 — 确保 create_all() 能建表

LOG = logging.getLogger("DBEngine")

DBS_DIR = "dbs"
os.makedirs(DBS_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DBS_DIR}/group_messages.db"
_DB_PATH = f"{DBS_DIR}/group_messages.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """初始化数据库，创建所有表并执行 schema migration"""
    try:
        Base.metadata.create_all(bind=engine)

        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

        LOG.info("Database initialized: %s", DATABASE_URL)
        migrate.run(_DB_PATH)


    except Exception as e:
        LOG.error("Failed to initialize database: %s", e)
        raise


def get_db() -> Generator[Session, None, None]:
    """获取数据库 Session（依赖注入用）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
