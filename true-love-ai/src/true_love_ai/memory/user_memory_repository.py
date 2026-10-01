# -*- coding: utf-8 -*-
"""
用户记忆数据访问层
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert

from true_love_ai.models.user_memory import UserMemory

LOG = logging.getLogger("UserMemoryRepository")

# key 格式: "category.sub_key"（如 interest.music）或无 dot 的特殊 key（如 timezone）
ALLOWED_CATEGORIES = {
    "personality", "occupation", "preference", "fact",
    "interest", "habit", "personal", "event",
}
SPECIAL_KEYS = {"timezone"}


def is_valid_key(key: str) -> bool:
    if key in SPECIAL_KEYS:
        return True
    category = key.split(".")[0]
    return category in ALLOWED_CATEGORIES


class UserMemoryRepository:
    """用户记忆仓储"""

    def __init__(self, session: Session):
        self.session = session

    def upsert(self, group_id: str, sender_id: str, key: str, value: str, source: str = None) -> bool:
        if not is_valid_key(key):
            LOG.warning("非法 memory key, 已忽略: %s", key)
            return False

        try:
            stmt = insert(UserMemory).values(
                group_id=group_id,
                sender_id=sender_id,
                key=key,
                value=value,
                source=source,
                updated_at=datetime.now(),
            ).on_conflict_do_update(
                index_elements=['group_id', 'sender_id', 'key'],
                set_={"value": value, "source": source, "updated_at": datetime.now()},
            )
            self.session.execute(stmt)
            self.session.commit()
            LOG.info("upsert memory: group=%s sender_id=%s key=%s", group_id, sender_id, key)
            return True
        except Exception as e:
            self.session.rollback()
            LOG.exception("upsert memory 失败: group=%s sender_id=%s key=%s err=%s", group_id, sender_id, key, e)
            return False

    def get_by_user(self, group_id: str, sender_id: str) -> list[dict]:
        """查询失败直接抛异常，调用方决定是当没有还是报失败"""
        rows = (
            self.session.query(UserMemory)
            .filter(UserMemory.group_id == group_id, UserMemory.sender_id == sender_id)
            .order_by(UserMemory.updated_at.desc())
            .all()
        )
        return [
            {
                "key": r.key,
                "value": r.value,
                "source": r.source,
                "updated_at": r.updated_at.strftime("%Y-%m-%d") if r.updated_at else None,
            }
            for r in rows
        ]
