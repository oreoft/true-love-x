# -*- coding: utf-8 -*-
import json
import logging
import hashlib
from dataclasses import asdict
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.group_message import GroupMessage

LOG = logging.getLogger("GroupMessageRepository")


class GroupMessageRepository:
    """一个机器人库里的聊天记录，session 来自 db_engine.bot_session(bot_id)"""

    def __init__(self, session: Session):
        self.session = session

    def save(self, msg) -> bool:
        try:
            msg_id_val = getattr(msg, "msg_id", "")
            msg_hash = msg_id_val or getattr(msg, "msg_hash", "") or self._fallback_hash(msg)
            group_msg = GroupMessage(
                msg_id=msg.msg_id,
                msg_hash=msg_hash,
                msg_type=msg.msg_type,
                sender_id=msg.sender_id,
                sender_name=msg.sender_name or msg.sender_id,
                chat_id=msg.chat_id,
                chat_name=msg.chat_name or msg.chat_id,
                content=msg.content,
                is_group=msg.is_group,
                is_at_me=msg.is_at_me,
                image_msg=self._serialize(msg.image_msg),
                voice_msg=self._serialize(msg.voice_msg),
                video_msg=self._serialize(msg.video_msg),
                file_msg=self._serialize(msg.file_msg),
                link_msg=self._serialize(msg.link_msg),
                refer_msg=self._serialize(msg.refer_msg),
            )
            self.session.add(group_msg)
            self.session.commit()
            LOG.info("saved msg: bot_id=%s msg_hash=%s", getattr(msg, "bot_id", ""), msg_hash)
            return True
        except IntegrityError:
            self.session.rollback()
            LOG.warning("duplicate msg ignored: msg_hash=%s", msg_hash)
            return False
        except Exception as e:
            self.session.rollback()
            LOG.error("save failed: %s", e)
            return True

    @staticmethod
    def _serialize(field) -> Optional[str]:
        if field is None:
            return None
        try:
            return json.dumps(asdict(field), ensure_ascii=False)
        except Exception as e:
            LOG.warning("serialize field failed: %s", e)
            return None

    @staticmethod
    def _fallback_hash(msg) -> str:
        raw = "|".join([
            getattr(msg, "chat_id", "") or "",
            getattr(msg, "msg_id", "") or "",
            getattr(msg, "sender_id", "") or "",
            getattr(msg, "msg_type", "") or "",
            getattr(msg, "content", "") or "",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_messages(self, chat_id: str, sender_id: str = None, sender_name: str = None,
                     limit: int = 100, tail_id: int = None, keyword: str = None) -> list[dict]:
        """chat_id 里 id 小于 tail_id 的最近 limit 条，按时间正序返回；tail_id 为空时从最新的开始"""
        try:
            query = self.session.query(GroupMessage).filter(GroupMessage.chat_id == chat_id)
            if keyword:
                query = query.filter(GroupMessage.content.contains(keyword))
            if sender_id:
                query = query.filter(GroupMessage.sender_id == sender_id)
            if sender_name:
                query = query.filter(GroupMessage.sender_name == sender_name)
            if tail_id is not None:
                query = query.filter(GroupMessage.id < tail_id)
            messages = query.order_by(GroupMessage.id.desc()).limit(limit).all()
            messages.reverse()
            return [
                {
                    "id": msg.id,
                    "msg_id": msg.msg_id,
                    "msg_type": msg.msg_type,
                    "chat_id": msg.chat_id,
                    "chat_name": msg.chat_name,
                    "sender_id": msg.sender_id,
                    "sender_name": msg.sender_name,
                    "content": msg.content,
                    "is_at_me": msg.is_at_me,
                    "created_at": msg.created_at.strftime('%Y-%m-%d %H:%M:%S') if msg.created_at else None,
                }
                for msg in messages
            ]
        except Exception as e:
            LOG.error("get_messages failed: chat_id=%s err=%s", chat_id, e)

            return []

    def list_chats(self) -> list[dict]:
        """库里出现过的会话，最近有消息的在前，后台按群浏览用"""
        rows = (
            self.session.query(
                GroupMessage.chat_id,
                func.max(GroupMessage.chat_name).label("chat_name"),
                func.max(GroupMessage.is_group).label("is_group"),
                func.count(GroupMessage.id).label("count"),
                func.max(GroupMessage.created_at).label("last_at"),
            )
            .group_by(GroupMessage.chat_id)
            .order_by(func.max(GroupMessage.id).desc())
            .all()
        )
        return [
            {
                "chat_id": row.chat_id,
                "chat_name": row.chat_name or row.chat_id,
                "is_group": bool(row.is_group),
                "count": row.count,
                "last_at": row.last_at.strftime('%Y-%m-%d %H:%M:%S') if row.last_at else None,
            }
            for row in rows
        ]

    def count_since(self, since) -> int:
        """since 之后收到的消息数"""
        return self.session.query(func.count(GroupMessage.id)).filter(GroupMessage.created_at >= since).scalar() or 0
