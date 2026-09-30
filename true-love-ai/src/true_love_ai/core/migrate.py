# -*- coding: utf-8 -*-
"""
AI DB 当前 Migration
已执行的版本记录在 schema_migrations 表。

有新 migration 时：直接替换此文件内容即可，旧版本已在 schema_migrations 里记录，不会重复执行。
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

LOG = logging.getLogger("migrate")

VERSION = "003"
DESCRIPTION = "personas / skill_permissions: import from the old config file"

# 旧代码里写死的语音风格，导入成所有机器人共用的默认
_OLD_VOICE_STYLE = "请用软萌可爱、俏皮活泼、带点小傲娇的16岁萝莉少女音色朗读，语调轻快、尾音略微上扬："
# 旧 prompt 里写死的名字换成 {name}，按机器人的昵称替换
_NAME_IN_PROMPT = re.compile(r"(你的名字叫做?)[^，,。！!\s]+")


def _old_config() -> dict:
    """旧配置文件里的 llm、skill_permissions 段；没有配置文件（如测试）时为空"""
    from true_love_ai.core.config import _config_path
    path = Path(_config_path())
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def migrate(conn: sqlite3.Connection, old: dict) -> None:
    """表由 create_all 建好；这里把旧配置里的默认 prompt 和技能权限导进来，都挂在所有机器人（"*"）下面"""
    now = datetime.now().isoformat()
    llm = old.get("llm") or {}
    prompt = _NAME_IN_PROMPT.sub(r"\1{name}", (llm.get("system_prompt") or "").strip(), count=1)
    conn.execute(
        "INSERT OR IGNORE INTO personas (bot_id, chat, prompt, voice_style, updated_at) VALUES ('*', '', ?, ?, ?)",
        (prompt, _OLD_VOICE_STYLE, now),
    )
    for skill, users in (old.get("skill_permissions") or {}).items():
        conn.execute(
            "INSERT OR IGNORE INTO skill_permissions (bot_id, skill, users, updated_at) VALUES ('*', ?, ?, ?)",
            (skill, json.dumps(list(users), ensure_ascii=False), now),
        )
    # 按群或人指定的 prompt 用的是以前的 wxid，现在消息里是昵称和群名，对不上，不导入
    skipped = llm.get("user_prompt_map") or {}
    if skipped:
        LOG.info("Migration %s: %d 条按群或人指定的 prompt 没导入（旧 id 对不上），需要时在 tl-admin 里按群名重新指定",
                 VERSION, len(skipped))


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
            migrate(conn, _old_config())
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
