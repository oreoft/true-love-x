#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话管理模块

Session 对象只持有元数据（session_id / TTL / 压缩状态），消息、技能调用过程和摘要都在 SQLite 里（见 session_repository），
每次用的时候现读，转成 pydantic_ai 的消息交给模型。
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from true_love_ai.core.background import spawn
from true_love_ai.core.config import get_config
from true_love_ai.memory.session_repository import MSG, TOOLS, StoredRow, get_session_repo

LOG = logging.getLogger("Session")

# 压缩失败后多久内不再重试，免得每来一条消息都调一次压缩模型
COMPRESS_RETRY_COOLDOWN = timedelta(minutes=10)
# 存进历史的技能结果最多这么长，免得一次网页搜索把之后每轮的上下文都撑大
STORED_TOOL_RESULT_CHARS = 2000


def to_model_messages(rows: list[StoredRow]) -> list[ModelMessage]:
    """库里的行 → 交给模型的历史；读不出来的技能过程跳过，不影响前后的对话"""
    messages: list[ModelMessage] = []
    for row in rows:
        if row.type == MSG and row.role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=row.content)]))
        elif row.type == MSG:
            messages.append(ModelResponse(parts=[TextPart(content=row.content)]))
        elif row.type == TOOLS:
            try:
                messages.extend(ModelMessagesTypeAdapter.validate_json(row.content))
            except Exception as e:
                LOG.warning("技能调用记录读不出来，跳过: row=%s err=%s", row.id, e)
    return messages


def tool_steps_json(steps: list[ModelMessage]) -> Optional[str]:
    """
    一轮里调技能的过程（模型的 tool call 和技能的结果）存成 JSON；没有完整的调用过程返回 None

    instructions 每次现算，不存；太长的技能结果截短。每个 tool call 都要有对应的结果，否则这段整个不存，
    免得下次交给模型时缺了结果被拒。
    """
    kept: list[ModelMessage] = []
    pending: set[str] = set()
    for message in steps:
        if isinstance(message, ModelResponse):
            if not any(isinstance(p, ToolCallPart) for p in message.parts):
                continue
            pending |= {p.tool_call_id for p in message.parts if isinstance(p, ToolCallPart)}
            kept.append(message)
        elif isinstance(message, ModelRequest):
            parts = []
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id in pending:
                    pending.discard(part.tool_call_id)
                    if isinstance(part, ToolReturnPart) and isinstance(part.content, str) \
                            and len(part.content) > STORED_TOOL_RESULT_CHARS:
                        part = ToolReturnPart(tool_name=part.tool_name, tool_call_id=part.tool_call_id,
                                              content=part.content[:STORED_TOOL_RESULT_CHARS] + "…（已截断）")
                    parts.append(part)
            if parts:
                kept.append(ModelRequest(parts=parts))
    if not kept or pending:
        return None
    return ModelMessagesTypeAdapter.dump_json(kept).decode()


def without_tool_steps(messages: list[ModelMessage]) -> list[ModelMessage]:
    """
    去掉历史里的技能调用过程，只留说过的话

    给这次没有任何技能可用的人用：有的模型（比如 Claude）不接受"历史里有工具调用、这次却没给工具定义"的请求
    """
    kept: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            parts = [p for p in message.parts if not isinstance(p, ToolCallPart)]
            if parts:
                kept.append(ModelResponse(parts=parts))
        else:
            parts = [p for p in message.parts if not isinstance(p, (ToolReturnPart, RetryPromptPart))]
            if parts:
                kept.append(ModelRequest(parts=parts))
    return kept


def _render_for_summary(row: StoredRow) -> list[str]:
    if row.type == MSG:
        return [f"{row.role}: {row.content[:800]}"]
    lines = []
    try:
        steps = ModelMessagesTypeAdapter.validate_json(row.content)
    except Exception:
        return lines
    for message in steps:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                lines.append(f"assistant 调用技能 {part.tool_name}({part.args_as_json_str()[:200]})")
            elif isinstance(part, ToolReturnPart):
                lines.append(f"技能 {part.tool_name} 返回: {str(part.content)[:300]}")
    return lines


class Session:
    """单个会话（纯 DB 存储消息）"""

    def __init__(
            self,
            session_id: str,
            ttl_seconds: int = 86400,
            compress_threshold: int = 50,
            compress_keep_recent: int = 10,
            compress_fn: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
    ):
        self.session_id = session_id
        self.ttl = timedelta(seconds=ttl_seconds)
        self._compress_threshold = compress_threshold
        self._compress_keep_recent = compress_keep_recent
        self._compress_fn = compress_fn
        self._compressing = False
        # 上次压缩失败后，到这个时间之前不再重试
        self._compress_retry_at: Optional[datetime] = None

        self.updated_at = datetime.now()

    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.updated_at + self.ttl

    def history(self) -> tuple[Optional[str], list[ModelMessage]]:
        """(摘要, 之前的对话)，交给模型用"""
        self.updated_at = datetime.now()
        summary, rows = get_session_repo().load(self.session_id)
        return summary, to_model_messages(rows)

    def record_turn(self, user_text: str, reply: Optional[str] = None,
                    tool_steps: Optional[list[ModelMessage]] = None) -> None:
        """
        记下这一轮：用户的话、调技能的过程、发出去的回复；没回复的（不回、自动触发时没发出去的）只记用户的话

        tool_steps 可以直接给这一轮 pydantic_ai 的全部新消息，只挑出技能调用和结果存
        """
        self.updated_at = datetime.now()
        steps_json = tool_steps_json(tool_steps or []) if reply else None
        get_session_repo().append_turn(self.session_id, user_text, steps_json, reply)
        self._maybe_compress()

    def _maybe_compress(self) -> None:
        if not self._should_compress():
            return
        if get_session_repo().count_messages(self.session_id) < self._compress_threshold:
            return
        try:
            spawn(self._compress(), f"compress:{self.session_id}")
        except RuntimeError:
            LOG.warning("Session %s 不在事件循环里，这次跳过压缩", self.session_id)

    def _should_compress(self) -> bool:
        if self._compressing or not self._compress_fn:
            return False
        return self._compress_retry_at is None or datetime.now() >= self._compress_retry_at

    @staticmethod
    def _cut(rows: list[StoredRow], keep: int) -> int:
        """
        要压掉的行数：最近 keep 条 msg 留着，往前退到一条用户消息开头，
        让留下的历史从一轮的开头开始，不会只剩技能调用过程或回复
        """
        msg_idx = [i for i, r in enumerate(rows) if r.type == MSG]
        if len(msg_idx) <= keep:
            return 0
        cut = msg_idx[-keep] if keep > 0 else len(rows)
        while cut < len(rows) and not (rows[cut].type == MSG and rows[cut].role == "user"):
            cut += 1
        return cut

    async def _compress(self):
        if self._compressing or not self._compress_fn:
            return
        self._compressing = True
        try:
            repo = get_session_repo()
            summary, rows = repo.load(self.session_id)
            cut = self._cut(rows, self._compress_keep_recent)
            to_compress = rows[:cut]
            if not to_compress:
                return

            lines = ["请将以下对话历史压缩为简洁摘要，保留所有关键信息、用户偏好和重要事件，用中文输出。"]
            if summary:
                lines.append(f"\n【已有摘要】\n{summary}")
            lines.append("\n【需压缩的对话记录】")
            for row in to_compress:
                lines.extend(_render_for_summary(row))

            new_summary = await self._compress_fn(
                [{"role": "user", "content": "\n".join(lines)}]
            )
            if not (new_summary or "").strip():
                raise ValueError("压缩模型返回了空摘要")

            # 只删被摘要过的那些行；压缩这段时间新来的消息 id 都更大，不受影响
            deleted = repo.compress(self.session_id, new_summary, to_compress[-1].id)
            self._compress_retry_at = None
            LOG.info("Session %s 压缩完成: 压掉 %d 行，保留 %d 行", self.session_id, deleted, len(rows) - cut)
        except Exception as e:
            self._compress_retry_at = datetime.now() + COMPRESS_RETRY_COOLDOWN
            LOG.exception("Session %s 压缩失败，%s 内不再重试: %s", self.session_id, COMPRESS_RETRY_COOLDOWN, e)
        finally:
            self._compressing = False


class SessionManager:
    """会话管理器（线程安全，Session 对象只存元数据）"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

        config = get_config()
        self.ttl_seconds = config.session.ttl_seconds
        self.compress_threshold = config.session.compress_threshold
        self.compress_keep_recent = config.session.compress_keep_recent

    def _make_compress_fn(self) -> Callable[[list[dict]], Awaitable[str]]:
        """创建压缩函数，注入 LLM 调用能力（避免 Session 直接依赖 llm_router）"""
        from true_love_ai.llm.router import get_llm_router
        llm = get_llm_router()

        async def _compress_fn(messages: list[dict]) -> str:
            return await llm.compress(messages)

        return _compress_fn

    def get_or_create(self, session_id: str) -> Session:
        """session_id: 会话 ID，"bot_id:群或人" """
        with self._lock:
            self._cleanup_expired()
            session = self._sessions.get(session_id)
            if session is None:
                session = self._sessions[session_id] = Session(
                    session_id=session_id,
                    ttl_seconds=self.ttl_seconds,
                    compress_threshold=self.compress_threshold,
                    compress_keep_recent=self.compress_keep_recent,
                    compress_fn=self._make_compress_fn(),
                )
                LOG.debug("创建新会话: %s", session_id)
            return session

    def _cleanup_expired(self):
        expired = [sid for sid, s in self._sessions.items() if s.is_expired]
        for sid in expired:
            del self._sessions[sid]
            LOG.debug("清理过期会话: %s", sid)


_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
