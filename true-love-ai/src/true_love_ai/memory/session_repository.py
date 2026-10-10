# -*- coding: utf-8 -*-
"""
对话历史持久化仓储

一个会话的行按 id 排列：
    summary  压缩出来的早期对话摘要（最多一行）
    msg      用户说的话（role=user）、发出去的回复（role=assistant），纯文本
    tools    一轮里模型调技能的过程（调了什么、技能回了什么），pydantic_ai 消息的 JSON，夹在这一轮的 user 和 assistant 之间

一轮对话在处理完时一起写进去（append_turn），中间不会插进别的消息。

压缩只按 id 删：删掉被摘要过的那一段，摘要期间新来的消息不受影响。
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.models.session_message import SessionMessage

LOG = logging.getLogger("SessionRepository")

MSG, TOOLS, SUMMARY = "msg", "tools", "summary"


@dataclass
class StoredRow:
    id: int
    type: str
    role: Optional[str]
    content: str


class SessionRepository:

    def load(self, session_id: str) -> tuple[Optional[str], list[StoredRow]]:
        """会话的摘要和 msg、tools 行。读库失败当作空会话"""
        try:
            with SessionLocal() as db:
                rows = (
                    db.query(SessionMessage)
                    .filter(SessionMessage.session_id == session_id)
                    .order_by(SessionMessage.id)
                    .all()
                )
        except Exception as e:
            LOG.exception("load session failed: session=%s err=%s", session_id, e)
            return None, []
        summary = None
        history = []
        for row in rows:
            if row.type == SUMMARY:
                summary = row.content
            else:
                history.append(StoredRow(row.id, row.type, row.role, row.content))
        return summary, history

    def count_messages(self, session_id: str) -> int:
        """一问一答各算一条，技能调用过程不算"""
        try:
            with SessionLocal() as db:
                return (
                    db.query(SessionMessage)
                    .filter(SessionMessage.session_id == session_id, SessionMessage.type == MSG)
                    .count()
                )
        except Exception as e:
            LOG.exception("count_messages failed: session=%s err=%s", session_id, e)
            return 0

    def append_message(self, session_id: str, role: str, content: str) -> Optional[int]:
        """返回新行的 id；写库失败返回 None"""
        try:
            with SessionLocal() as db:
                row = SessionMessage(session_id=session_id, type=MSG, role=role, content=content,
                                     created_at=datetime.now())
                db.add(row)
                db.commit()
                return row.id
        except Exception as e:
            LOG.exception("append_message failed: session=%s err=%s", session_id, e)
            return None

    def append_turn(self, session_id: str, user_text: str, tools_json: Optional[str], reply: Optional[str]) -> None:
        """
        一轮对话一起写：用户的话、技能调用过程（有的话）、发出去的回复（有的话）
        回复为空时历史里技能结果后面直接接下一条用户消息，pydantic_ai 会把它们并成一次请求，模型都接受

        一起写是为了同一个群里同时处理的两条消息不会交错成 用户A、用户B、技能A、回复A……，
        那样技能调用就不紧跟在提问后面，有的模型会拒收这样的历史
        """
        try:
            with SessionLocal() as db:
                now = datetime.now()
                db.add(SessionMessage(session_id=session_id, type=MSG, role="user", content=user_text,
                                      created_at=now))
                if tools_json:
                    db.add(SessionMessage(session_id=session_id, type=TOOLS, role=None, content=tools_json,
                                          created_at=now))
                if reply:
                    db.add(SessionMessage(session_id=session_id, type=MSG, role="assistant", content=reply,
                                          created_at=now))
                db.commit()
        except Exception as e:
            LOG.exception("append_turn failed: session=%s err=%s", session_id, e)

    def compress(self, session_id: str, new_summary: str, upto_id: int) -> int:
        """
        删掉 id <= upto_id 的 msg、tools 行，写入新摘要；返回删了几行
        失败抛异常，由压缩流程记日志并进入冷却
        """
        with SessionLocal() as db:
            deleted = (
                db.query(SessionMessage)
                .filter(SessionMessage.session_id == session_id, SessionMessage.type != SUMMARY,
                        SessionMessage.id <= upto_id)
                .delete(synchronize_session=False)
            )
            summary_row = (
                db.query(SessionMessage)
                .filter(SessionMessage.session_id == session_id, SessionMessage.type == SUMMARY)
                .first()
            )
            if summary_row:
                summary_row.content = new_summary
                summary_row.created_at = datetime.now()
            else:
                db.add(SessionMessage(session_id=session_id, type=SUMMARY, role=None, content=new_summary,
                                      created_at=datetime.now()))
            db.commit()
            return deleted


_repo: SessionRepository | None = None


def get_session_repo() -> SessionRepository:
    global _repo
    if _repo is None:
        _repo = SessionRepository()
    return _repo
