# -*- coding: utf-8 -*-
"""
Database Engine - 数据库引擎

每个机器人一个 SQLite 库 dbs/<bot_id>.db：聊天记录、监听列表、提醒和定时任务都在里面，机器人之间物理隔离。
平台库 dbs/platform.db 只放所有机器人共用的数据：机器人登记表。
"""

import logging
import re
import threading
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import migrate
from ..models.bot import PlatformBase
from ..models.group_message import Base
from ..models.schema_migration import SchemaMigration  # noqa: F401 — 确保 create_all() 能建表
from ..models.listen_chat import ListenChat  # noqa: F401 — 确保 create_all() 能建表
from ..models.bot_setting import BotSetting  # noqa: F401 — 确保 create_all() 能建表

LOG = logging.getLogger("DBEngine")

DBS_DIR = Path("dbs")
PLATFORM_DB = "platform.db"

# bot_id 会拼进文件名，只允许这些字符
_BOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_lock = threading.Lock()
_platform: sessionmaker | None = None
_bots: dict[str, tuple[Engine, sessionmaker]] = {}


def _create_engine(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False}, echo=False)

    @event.listens_for(engine, "connect")
    def _wal(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    return engine


def check_bot_id(bot_id: str) -> str:
    """bot_id 合法时原样返回，否则抛 ValueError"""
    if not isinstance(bot_id, str) or not _BOT_ID.match(bot_id):
        raise ValueError(f"非法的 bot_id: {bot_id!r}")
    return bot_id


def bot_db_path(bot_id: str) -> Path:
    return DBS_DIR / f"{check_bot_id(bot_id)}.db"


def init_platform_db() -> None:
    """建平台库的表，启动时调用一次"""
    global _platform
    with _lock:
        if _platform is not None:
            return
        DBS_DIR.mkdir(parents=True, exist_ok=True)
        engine = _create_engine(DBS_DIR / PLATFORM_DB)
        PlatformBase.metadata.create_all(bind=engine)
        _platform = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        LOG.info("Platform database initialized: %s", DBS_DIR / PLATFORM_DB)


def init_bot_db(bot_id: str) -> Engine:
    """打开（第一次时新建）这个机器人的库，建表并执行 migration；已经打开过的直接返回"""
    with _lock:
        if bot_id in _bots:
            return _bots[bot_id][0]
        path = bot_db_path(bot_id)
        DBS_DIR.mkdir(parents=True, exist_ok=True)
        engine = _create_engine(path)
        Base.metadata.create_all(bind=engine)
        migrate.run(str(path))
        _bots[bot_id] = (engine, sessionmaker(autocommit=False, autoflush=False, bind=engine))
        LOG.info("Bot database initialized: %s", path)
        return engine


def platform_session() -> Session:
    if _platform is None:
        raise RuntimeError("平台库还没有初始化")
    return _platform()


def bot_session(bot_id: str) -> Session:
    """这个机器人的库；机器人登记时才会建库，没登记过的抛 KeyError"""
    entry = _bots.get(bot_id)
    if entry is None:
        raise KeyError(f"机器人 {bot_id} 的库还没有打开")
    return entry[1]()


def bot_engine(bot_id: str) -> Engine:
    entry = _bots.get(bot_id)
    if entry is None:
        raise KeyError(f"机器人 {bot_id} 的库还没有打开")
    return entry[0]


def reset() -> None:
    """关掉所有库，测试换数据目录时用"""
    global _platform
    with _lock:
        for engine, _ in _bots.values():
            engine.dispose()
        _bots.clear()
        _platform = None
