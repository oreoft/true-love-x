# -*- coding: utf-8 -*-
"""
对话历史持久化仓储
"""

import logging
from datetime import datetime

from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.models.session_message import SessionMessage

LOG = logging.getLogger("SessionRepository")


class SessionRepository:

    def load(self, session_id: str) -> tuple[str | None, list[dict]]:
        """加载 session 的摘要和消息列表"""
        try:
            with SessionLocal() as db:
                rows = (
                    db.query(SessionMessage)
                    .filter(SessionMessage.session_id == session_id)
                    .order_by(SessionMessage.id)
                    .all()
                )
            summary = None
            messages = []
            for row in rows:
                if row.type == 'summary':
                    summary = row.content
                else:
                    messages.append({"role": row.role, "content": row.content})
            return summary, messages
        except Exception as e:
            LOG.exception("load session failed: session=%s err=%s", session_id, e)
            return None, []

    def count_messages(self, session_id: str) -> int:
        try:
            with SessionLocal() as db:
                return (
                    db.query(SessionMessage)
                    .filter(SessionMessage.session_id == session_id, SessionMessage.type == 'msg')
                    .count()
                )
        except Exception as e:
            LOG.exception("count_messages failed: session=%s err=%s", session_id, e)
            return 0

    def append_message(self, session_id: str, role: str, content: str) -> None:
        try:
            with SessionLocal() as db:
                db.add(SessionMessage(
                    session_id=session_id,
                    type='msg',
                    role=role,
                    content=content,
                    created_at=datetime.now(),
                ))
                db.commit()
        except Exception as e:
            LOG.exception("append_message failed: session=%s err=%s", session_id, e)

    def compress(self, session_id: str, new_summary: str, keep_count: int) -> None:
        """删除旧 msg 行（保留最近 keep_count 条），upsert summary 行；失败抛异常，由压缩流程记日志并进入冷却"""
        with SessionLocal() as db:
            msg_rows = (
                db.query(SessionMessage)
                .filter(SessionMessage.session_id == session_id, SessionMessage.type == 'msg')
                .order_by(SessionMessage.id)
                .all()
            )
            to_delete = msg_rows[:-keep_count] if len(msg_rows) > keep_count else []
            for row in to_delete:
                db.delete(row)

            summary_row = (
                db.query(SessionMessage)
                .filter(SessionMessage.session_id == session_id, SessionMessage.type == 'summary')
                .first()
            )
            if summary_row:
                summary_row.content = new_summary
                summary_row.created_at = datetime.now()
            else:
                db.add(SessionMessage(
                    session_id=session_id,
                    type='summary',
                    role=None,
                    content=new_summary,
                    created_at=datetime.now(),
                ))
            db.commit()


_repo: SessionRepository | None = None


def get_session_repo() -> SessionRepository:
    global _repo
    if _repo is None:
        _repo = SessionRepository()
    return _repo
