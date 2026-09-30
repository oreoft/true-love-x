# -*- coding: utf-8 -*-
"""
Server DB 当前 Migration
已执行过的版本记录在 schema_migrations 表里。

有新 migration 时：直接替换此文件内容即可，旧版本已在 schema_migrations 里记录，不会重复执行。
"""

import sqlite3


# 后台设置页已删除：唯一的设置项"本机回调地址"改由 true_love_common.hosts 按机器人登记
VERSION = "003"
DESCRIPTION = "settings: drop the settings table"


def migrate(conn: sqlite3.Connection) -> None:
    """删掉不再使用的 settings 表"""
    conn.execute("DROP TABLE IF EXISTS settings")


def run(db_path: str) -> None:
    """幂等执行当前 migration，已应用则跳过"""
    import logging
    log = logging.getLogger("migrate")

    conn = sqlite3.connect(db_path)
    try:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        if VERSION in applied:
            log.info("Migration %s: already applied, skipping", VERSION)
            return
        log.info("Migration %s: applying — %s", VERSION, DESCRIPTION)
        try:
            migrate(conn)
            from datetime import datetime
            conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (VERSION, DESCRIPTION, datetime.now().isoformat()),
            )
            conn.commit()
            log.info("Migration %s: done", VERSION)
        except Exception as e:
            conn.rollback()
            log.error("Migration %s: failed — %s", VERSION, e)
            raise
    finally:
        conn.close()
