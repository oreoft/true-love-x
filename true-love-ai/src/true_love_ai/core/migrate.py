# -*- coding: utf-8 -*-
"""
AI DB 当前 Migration
已执行的版本记录在 schema_migrations 表。

有新 migration 时：直接替换此文件内容即可，旧版本已在 schema_migrations 里记录，不会重复执行。
"""

import json
import logging
import sqlite3
from datetime import datetime

LOG = logging.getLogger("migrate")

VERSION = "004"
DESCRIPTION = "skill_access: one platform-wide permission list per skill, points written as platform:bot:group:person"


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def convert(entry: str, bot_id: str) -> str:
    """旧权限 "平台:人" / "平台:*" / "*"（外加它属于哪个号，"*" 是所有号）→ 新的四段 "平台:号:群:人" """
    entry = entry.strip()
    if entry == "*":
        return f"*:{bot_id}:*:*" if bot_id != "*" else "*:*:*:*"
    platform, _, user = entry.partition(":")
    return f"{platform}:{bot_id}:*:{user or '*'}"


def migrate(conn: sqlite3.Connection) -> None:
    """skill_access、ai_settings 由 create_all 建好；这里把旧的按号规则和动态技能自带的权限搬进来"""
    now = datetime.now().isoformat()
    points: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}

    if "skill_permissions" in _tables(conn):
        for bot_id, skill, users in conn.execute("SELECT bot_id, skill, users FROM skill_permissions"):
            for entry in json.loads(users or "[]"):
                point = convert(entry, bot_id)
                if point not in points.setdefault(skill, []):
                    points[skill].append(point)
            kinds[skill] = "builtin"

    # 动态技能自带的权限以前优先于规则；没带的以前是所有人能用
    if "dynamic_skills" in _tables(conn) and "permissions" in _cols(conn, "dynamic_skills"):
        for skill_id, perms in conn.execute("SELECT id, permissions FROM dynamic_skills"):
            own = [convert(e, "*") for e in json.loads(perms)] if perms else None
            points[skill_id] = own or points.get(skill_id) or ["*:*:*:*"]
            kinds[skill_id] = "installed"

    for skill, pts in points.items():
        conn.execute("INSERT OR IGNORE INTO skill_access (skill, kind, points, updated_at) VALUES (?, ?, ?, ?)",
                     (skill, kinds[skill], json.dumps(pts, ensure_ascii=False), now))

    # 新内置技能默认给管理员：沿用"保存技能"这个管理类技能现在的权限，没有就所有人
    default = points.get("skill_save") or ["*:*:*:*"]
    conn.execute("INSERT OR IGNORE INTO ai_settings (key, value, updated_at) VALUES ('new_builtin_skill_points', ?, ?)",
                 (json.dumps(default, ensure_ascii=False), now))

    if "skill_permissions" in _tables(conn):
        conn.execute("DROP TABLE skill_permissions")
    LOG.info("Migration %s: 搬了 %d 个技能的权限，新内置技能默认 %s", VERSION, len(points), default)


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
            conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (VERSION, DESCRIPTION, datetime.now().isoformat()),
            )
            conn.commit()
            LOG.info("Migration %s: done", VERSION)
        except Exception as e:
            conn.rollback()
            LOG.error("Migration %s: failed — %s", VERSION, e)
            raise
    finally:
        conn.close()
