#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话管理模块
Session 对象只持有元数据（session_id / system_prompt / TTL），
消息和摘要全部走 SQLite，不在内存中缓存。
"""
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable
from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.memory.dynamic_skill_repository import DynamicSkillRepository
from true_love_ai.agent.skills.permission import check_permission
import json
from true_love_ai.core.config import get_config
from true_love_ai.memory import persona_service

LOG = logging.getLogger(__name__)


def _load_dynamic_skill_hints(platform: str = "", sender_id: str = "", bot_id: str = "") -> str:
    """从 DB 读取当前用户有权限的动态技能，返回注入 prompt 的文本。"""
    try:

        with SessionLocal() as db:
            skills = DynamicSkillRepository(db).list_all()
        if not skills:
            return ""
        ctx = {"platform": platform, "sender_id": sender_id, "bot_id": bot_id}
        visible = []
        for s in skills:
            perms = json.loads(s.permissions) if s.permissions else None
            if check_permission(s.id, ctx, perms):
                visible.append(f"- {s.id}（{s.name}）: {s.description}")
        return "\n".join(visible)
    except Exception as e:
        LOG.warning("加载动态技能列表失败: %s", e)
        return ""


class Session:
    """单个会话（纯 DB 存储消息）"""

    def __init__(
            self,
            session_id: str,
            system_prompt: str,
            ttl_seconds: int = 86400,
            compress_threshold: int = 50,
            compress_keep_recent: int = 10,
            compress_fn: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
    ):
        self.session_id = session_id
        self.system_prompt = system_prompt
        self.ttl = timedelta(seconds=ttl_seconds)
        self._compress_threshold = compress_threshold
        self._compress_keep_recent = compress_keep_recent
        self._compress_fn = compress_fn
        self._compressing = False

        self.updated_at = datetime.now()

    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.updated_at + self.ttl

    def add_message(self, role: str, content: str):
        self.updated_at = datetime.now()

        from true_love_ai.memory.session_repository import get_session_repo
        repo = get_session_repo()
        repo.append_message(self.session_id, role, content)

        count = repo.count_messages(self.session_id)
        if count >= self._compress_threshold and not self._compressing and self._compress_fn:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._compress())
            except RuntimeError:
                pass

    async def _compress(self):
        if self._compressing or not self._compress_fn:
            return
        self._compressing = True
        try:
            from true_love_ai.memory.session_repository import get_session_repo
            repo = get_session_repo()
            summary, all_msgs = repo.load(self.session_id)

            keep = self._compress_keep_recent
            to_compress = all_msgs[:-keep]
            if not to_compress:
                return

            lines = ["请将以下对话历史压缩为简洁摘要，保留所有关键信息、用户偏好和重要事件，用中文输出。"]
            if summary:
                lines.append(f"\n【已有摘要】\n{summary}")
            lines.append("\n【需压缩的对话记录】")
            for m in to_compress:
                lines.append(f"{m['role']}: {m['content'][:800]}")

            new_summary = await self._compress_fn(
                [{"role": "user", "content": "\n".join(lines)}]
            )

            repo.compress(self.session_id, new_summary, keep)
            LOG.info("Session %s 压缩完成: %d → %d msgs", self.session_id, len(all_msgs), keep)
        except Exception as e:
            LOG.error("Session %s 压缩失败: %s", self.session_id, e)
        finally:
            self._compressing = False

    def get_current_time_context(self) -> str:
        import re
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        tz_str = "Asia/Shanghai"
        if self.system_prompt:
            match = re.search(r"时区[：:]\s*([^\s\|]+)", self.system_prompt)
            if match:
                tz_str = match.group(1).strip()

        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz_str = "Asia/Shanghai"
            tz = ZoneInfo(tz_str)

        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(tz)

        return (
            f"系统当前世界标准时间(UTC): {now_utc.strftime('%Y-%m-%d %H:%M:%S')}。\n"
            f"该用户当前当地时间(时区={tz_str}): {now_local.strftime('%Y-%m-%d %H:%M:%S')}。\n"
            f"【极其重要】：如果用户要求你进行「x分钟后」、「明天几点」等时间推算，请**直接以系统告诉你的【该用户当地时间】为起点**进行相加减。\n"
            f"计算出的结果绝对不要再额外进行时差加减偏移！最后务必将你的结果转化为标准 ISO-8601 带时区的格式输出（例如：2026-04-13T10:30:00-05:00）。"
        )

    @staticmethod
    def _cacheable(text: str) -> list[dict]:
        """把文本包装成带 cache_control 的 content block（支持 Anthropic prompt caching）"""
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def get_messages_for_llm(self, platform: str = "", sender_id: str = "", bot_id: str = "") -> list[dict]:
        from true_love_ai.memory.session_repository import get_session_repo
        from true_love_ai.agent.skill_registry import get_all_tool_schemas

        summary, messages = get_session_repo().load(self.session_id)

        result = []

        if self.system_prompt:
            result.append({"role": "system", "content": self._cacheable(self.system_prompt)})

        if summary:
            result.append({
                "role": "system",
                "content": self._cacheable(
                    f"【早期对话摘要】以下是本次会话早期对话的压缩记录，供参考上下文：\n{summary}"
                ),
            })

        skills = get_all_tool_schemas(platform=platform, sender_id=sender_id, bot_id=bot_id)
        skill_text = "\n".join(f"- {s['function']['name']}: {s['function']['description']}" for s in skills)

        dynamic_skill_text = _load_dynamic_skill_hints(platform=platform, sender_id=sender_id, bot_id=bot_id)
        dynamic_section = (
            f"\n\n【动态技能列表】（通过 skill_run 执行，通过 skill_save 新增）\n{dynamic_skill_text}"
            if dynamic_skill_text else ""
        )

        time_hint = (
            f"{self.get_current_time_context()}\n"
            f"你已接入多模态 Agent 系统，能够执行特定的技能任务。\n"
            f"【重要】在群聊中，当用户使用「这个/那个/他说的/之前提到的/刚才讲的」等指代词，"
            f"且当前对话上下文中找不到对应内容时，必须先调用 fetch_group_context 补充群聊记录后再回答，不得猜测或编造。\n"
            f"如果你想知道你具备哪些拓展技能能力，以下是当前加载的专属技能列表：\n{skill_text}"
            f"{dynamic_section}\n"
        )
        result.append({"role": "system", "content": time_hint})

        result.extend(messages)
        return result


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

    def get_or_create(
            self,
            session_id: str,
            user_ctx: Optional[str] = None,
            bot_id: str = "",
            chat: str = "",
            bot_name: str = "",
    ) -> Session:
        """
        Args:
            session_id: 会话 ID，"bot_id:群或人"
            user_ctx: 发送者的画像，拼进 system prompt
            bot_id / chat: 选人设用：这个机器人里的群或私聊对象
            bot_name: 机器人的昵称，换掉人设里的 {name}
        """
        # 人设每次现查，tl-admin 改了下一条消息就生效
        prompt = persona_service.resolve(bot_id, chat, bot_name).prompt
        if user_ctx:
            prompt = f"{prompt}\n\n## 关于发送者的已知信息\n{user_ctx}"
        prompt += "\n\n## 回复格式\n微信不支持Markdown渲染，回复只能用纯文本和换行符，不能出现**加粗**、#标题、`代码块`、- 列表、> 引用等任何Markdown符号。"

        with self._lock:
            self._cleanup_expired()
            session = self._sessions.get(session_id)
            if session is None:
                session = self._sessions[session_id] = Session(
                    session_id=session_id,
                    system_prompt=prompt,
                    ttl_seconds=self.ttl_seconds,
                    compress_threshold=self.compress_threshold,
                    compress_keep_recent=self.compress_keep_recent,
                    compress_fn=self._make_compress_fn(),
                )
                LOG.debug("创建新会话: %s, has_user_ctx=%s", session_id, bool(user_ctx))
            else:
                session.system_prompt = prompt
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
