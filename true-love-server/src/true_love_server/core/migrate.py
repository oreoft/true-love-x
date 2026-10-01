# -*- coding: utf-8 -*-
"""
Server DB 当前 Migration
已执行过的版本记录在 schema_migrations 表里。

有新 migration 时：直接替换此文件内容即可，旧版本已在 schema_migrations 里记录，不会重复执行。
"""

import logging
import sqlite3

LOG = logging.getLogger("Migrate")

# 每个机器人一个库以后，库里的聊天记录都来自同一个机器人，平台由机器人登记表决定，不再每行存一遍
VERSION = "004"
DESCRIPTION = "group_messages: drop the platform column"


def migrate(conn: sqlite3.Connection) -> None:
    """删掉 group_messages 的 platform 列和用到它的索引；新建的库本来就没有，跳过"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(group_messages)")}
    conn.execute("DROP INDEX IF EXISTS idx_platform_chat")
    if "platform" in columns:
        conn.execute("ALTER TABLE group_messages DROP COLUMN platform")


def run(db_path: str) -> None:
    """幂等执行当前 migration，已应用则跳过"""
    conn = sqlite3.connect(db_path)
    try:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        if VERSION in applied:
            LOG.info("Migration %s: already applied, skipping", VERSION)
            return
        LOG.info("Migration %s: applying — %s", VERSION, DESCRIPTION)
        try:
            migrate(conn)
            from datetime import datetime
            conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (VERSION, DESCRIPTION, datetime.now().isoformat()),
            )
            conn.commit()
            LOG.info("Migration %s: done", VERSION)
        except Exception:
            conn.rollback()
            LOG.exception("Migration %s: failed", VERSION)
            raise
    finally:
        conn.close()
