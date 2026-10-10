# -*- coding: utf-8 -*-
"""
Agent Loop

核心 Agent 执行引擎：接收消息 → 构建上下文 → pydantic_ai Agent（LLM + 技能循环）→ 发送回复

流程：
    收到 msg
      → 消息转成文字（链接、图片、文件、语音、引用）
      → 取出之前的历史（含之前调技能的过程）和摘要
      → instructions（人设、规则、摘要）+ 历史 + 最新消息（时间、发送者画像、这次的话）交给 pydantic_ai
      → 模型调技能就执行（见 agent.tools），直到它给出文字回答；技能轮次到上限后让它别再调（tool_choice=none），直接回答
      → finish：按结局决定发不发、发什么，通过 Server /action/send 发送；这一轮（用户的话、技能过程、发出去的回复）一起记进历史
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from pydantic_ai import Agent, RunContext, capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import link_reader, outcome, prompt, server_client
from true_love_ai.agent.outcome import Ending, Outcome
from true_love_ai.agent.tools import TOOL_ARG_RETRIES, AgentDeps, SkillToolset
from true_love_ai.core.session import Session, get_session_manager, without_tool_steps
from true_love_ai.llm.router import get_llm_router
from true_love_ai.memory import persona_service
from true_love_ai.memory.memory_manager import get_user_context

LOG = logging.getLogger("AgentLoop")

# 一条消息里最多调几轮技能（每轮可以并行调多个）；到了上限让模型别再调，拿已有结果直接回答
MAX_TOOL_ITERATIONS = 6
# 模型请求次数的硬上限：技能轮次 + 最后回答 + 参数出错重来的余量，超了按 too_many_rounds 收尾
MAX_MODEL_REQUESTS = MAX_TOOL_ITERATIONS + 4
AUTO_REPLY_RULE = prompt.AUTO_REPLY_RULE
# 图片、文件消息没带用户自己的话时，替用户提的要求
IMAGE_REQUEST = "请看看这张图片，说说上面是什么内容"
FILE_REQUEST = "请看看这个文件，说说主要内容"


def _is_image_placeholder(content: str) -> bool:
    """微信图片消息的 content 是"图片"，飞书是 {"image_key": ...}"""
    text = (content or "").strip()
    return text in ("", "图片", "[图片]") or (text.startswith("{") and "image_key" in text)


def _is_file_placeholder(content: str) -> bool:
    """微信文件消息的 content 是卡片文字："文件\n文件名\n大小\n…" """
    text = (content or "").strip()
    return text in ("", "文件", "[文件]") or text.startswith("文件\n")


def is_auto_triggered(msg: ChatMsg) -> bool:
    """
    这条消息是不是群里没人 @、由 server 按后台开关自动交给 AI 的（链接、PDF、图片、笔记）

    群消息只有 @ 了机器人或者自动触发两种情况会到这里，所以群里没 @ 就是自动触发。
    自动触发时怎么表现不同（比如不发技能的"正在…"提示），都以这里为准，别在别处再判断一遍。
    """
    return msg.is_group and not msg.is_at_me


def _route(msg: ChatMsg) -> tuple[str, str, str]:
    """回复发给谁：(接收者, 要 @ 的人, 回的是哪条消息)；群里回复带上原消息，base 按设置 @、拍一拍或引用对方"""
    if msg.is_group:
        return msg.chat_id, msg.sender_id, msg.msg_id
    return msg.sender_id, "", ""


def _clean_content(content: str, mention: str = "") -> str:
    """去掉正文里叫机器人的那段文字；是哪段文字由 base 识别后随消息带来"""
    if mention:
        content = content.replace(mention, "", 1)
    return content.strip()


def _instructions(ctx: RunContext[AgentDeps]) -> str:
    return ctx.deps.instructions


def _model_settings(ctx: RunContext[AgentDeps]) -> ModelSettings:
    """
    技能轮次用完后让模型别再调技能、直接回答

    工具定义照常带上：历史里有工具调用时，有的模型（比如 Claude）要求请求里也有工具定义；前缀不变也利于缓存
    """
    return {"tool_choice": "none"} if ctx.deps.rounds_used_up(ctx.run_step) else {}


def _last_tool_result(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        for part in reversed(message.parts):
            if isinstance(part, ToolReturnPart):
                return str(part.content)
    return ""


def _tool_rounds(messages: list[ModelMessage]) -> int:
    return sum(1 for m in messages if isinstance(m, ModelResponse) and any(isinstance(p, ToolCallPart) for p in m.parts))


@dataclass
class Turn:
    """
    一条消息处理到哪了：结局、会话和这次用户的话（读懂消息、找到会话之后才有）、这轮 pydantic_ai 的新消息

    _think 边走边填，中途出错时 finish 也能把已经知道的记下来
    """
    ending: Ending
    session: Optional[Session] = None
    user_text: str = ""
    steps: list[ModelMessage] = field(default_factory=list)


class AgentLoop:
    """Agent 执行引擎（单例使用）"""

    def __init__(self, llm_router=None, session_manager=None):
        self.llm_router = llm_router or get_llm_router()
        self.session_manager = session_manager or get_session_manager()
        # 模型每条消息现取（tl-admin 改了立即生效），工具每一步按权限现筛（见 SkillToolset）
        self.agent = Agent(output_type=str | None, deps_type=AgentDeps, instructions=_instructions,
                           retries=TOOL_ARG_RETRIES, defer_model_check=True, name="tl-ai")
        self.toolset = SkillToolset()

    async def run(self, msg: ChatMsg) -> None:
        """处理一条消息：_think 得出结局，finish 统一收尾"""
        # 会话按机器人隔离：同名的群在不同的号里是不同的会话；用户画像跟着会话走（每个群一份、私聊一份）
        session_chat = msg.chat_id if msg.is_group else msg.sender_id
        session_id = f"{msg.bot_id}:{session_chat}"
        auto = is_auto_triggered(msg)

        LOG.info("AgentLoop.run: bot_id=%s platform=%s sender_id=%s sender_name=%s session=%s type=%s auto=%s",
                 msg.bot_id, msg.platform, msg.sender_id, msg.sender_name or msg.sender_id, session_id,
                 msg.msg_type, auto)

        turn = Turn(Ending(Outcome.CRASHED))
        try:
            await self._think(msg, session_id, session_chat, auto, turn)
        except Exception as e:
            LOG.exception("处理消息时出错: %s", e)
            turn.ending = Ending(Outcome.CRASHED, detail=repr(e)[:200])
        await self.finish(msg, turn.ending, turn)

    async def _think(self, msg: ChatMsg, session_id: str, session_chat: str, auto: bool, turn: Turn) -> None:
        """跑一遍 LLM + 技能循环，把结局填进 turn；不发回复，发不发、发什么由 finish 统一决定"""
        content = await self._build_user_content(msg)
        if not content:
            turn.ending = Ending(Outcome.UNREADABLE)
            return

        sender_name = msg.sender_name or msg.sender_id
        turn.session = self.session_manager.get_or_create(session_id)
        turn.user_text = prompt.stored_text(content, sender_name, msg.is_group)
        user_ctx = get_user_context(session_id, msg.sender_id)
        summary, history = turn.session.history()

        receiver, at_user, _ = _route(msg)
        chat = msg.chat_id if msg.is_group else ""
        # 人设按"这个机器人里的这个群或人"选，名字用 base 带来的昵称；每次现查，tl-admin 改了下一条就生效
        persona = persona_service.resolve(msg.bot_id, session_chat, msg.bot_name).prompt
        deps = AgentDeps(
            skill_ctx={
                "session_id": session_id, "sender_id": msg.sender_id, "sender_name": sender_name,
                "is_group": msg.is_group, "receiver": receiver, "at_user": at_user, "platform": msg.platform,
                "bot_id": msg.bot_id,
                # 权限点按群匹配用；私聊为空
                "chat": chat,
            },
            # 权限点按平台、号、群、人匹配
            access={"platform": msg.platform, "bot_id": msg.bot_id, "sender_id": msg.sender_id,
                    "is_group": msg.is_group, "chat": chat},
            instructions=prompt.instructions(persona, summary),
            # 没人叫它、自动接话时不发技能的"正在…"提示，免得群里一张图刷两条
            notify=not auto,
            max_tool_rounds=MAX_TOOL_ITERATIONS,
        )
        history = without_tool_steps(history, deps.allowed_skills())

        # 出错时也要拿到这轮已经发生的事（比如已经设好的提醒），按 run_id 从捕获的消息里挑出这一轮的
        run_id = uuid.uuid4().hex
        with capture_run_messages() as captured:
            try:
                result = await self.agent.run(
                    prompt.live_prompt(turn.user_text, sender_name=sender_name, user_ctx=user_ctx, auto=auto),
                    message_history=history,
                    deps=deps,
                    model=self.llm_router.agent_model(),
                    model_settings=_model_settings,
                    toolsets=[self.toolset],
                    usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
                    run_id=run_id,
                )
            except UsageLimitExceeded as e:
                LOG.warning("AgentLoop 超过最大请求次数 (%d): %s", MAX_MODEL_REQUESTS, e)
                turn.ending = Ending(Outcome.TOO_MANY_ROUNDS, detail=str(e)[:200])
                turn.steps = [m for m in captured if m.run_id == run_id]
                return
            except Exception as e:
                LOG.exception("LLM 调用失败: %s", e)
                turn.ending = Ending(Outcome.LLM_ERROR, detail=repr(e)[:200])
                turn.steps = [m for m in captured if m.run_id == run_id]
                return

        turn.steps = result.new_messages()
        if _tool_rounds(turn.steps) >= MAX_TOOL_ITERATIONS:
            LOG.warning("技能调用到了 %d 轮上限，模型按已有结果直接回答", MAX_TOOL_ITERATIONS)
        turn.ending = outcome.from_model_reply(result.output, _last_tool_result(turn.steps), auto,
                                               deps.unrecovered_failures())

    async def finish(self, msg: ChatMsg, ending: Ending, turn: Optional[Turn] = None) -> None:
        """
        一条消息的唯一收口：按结局决定发不发、发什么，打一行"回复结局"日志

        这一轮记进会话历史：用户说的话、调技能的过程，真发出去了再加上回复。
        不回、自动触发时的各种兜底、发送失败的都不记回复，免得下一轮模型以为用户看到了。
        """
        auto = is_auto_triggered(msg)
        text = outcome.text_to_send(ending, auto)
        sent = False
        if text:
            receiver, at_user, reply_msg_id = _route(msg)
            sent = await self._send_reply(receiver, text, at_user, reply_msg_id)
        if turn is not None and turn.session is not None:
            turn.session.record_turn(turn.user_text, text if sent else None, turn.steps)
        outcome.log_outcome(ending, bot_id=msg.bot_id, chat_id=msg.chat_id, sender_id=msg.sender_id,
                            msg_type=msg.msg_type, auto=auto, sent=bool(sent))

    async def _build_user_content(self, msg: ChatMsg) -> Optional[str]:
        """把各类消息类型转换为 LLM 可理解的文本"""
        msg_type = msg.msg_type
        content = _clean_content(msg.content, msg.mention)

        if msg_type == "text":
            url = self._extract_first_link(content)
            if url:
                crawled = await link_reader.read(url)
                if crawled:
                    return f"{content}\n\n[链接内容]\n{crawled}"
            return content or None

        if msg_type == "voice":
            text = msg.voice_msg.text_content if msg.voice_msg else ""
            return f"[语音转文字]: {text}" if text else None

        # 图片、文件消息的 content 多半只是平台的占位文字，不明说要求的话模型不去看，反过来问用户要干什么；
        # 有的渠道图片、文件能带用户自己的话，那就照用户的
        if msg_type == "image":
            resource = msg.image_msg.resource if msg.image_msg else None
            ref = resource.ref if resource else ""
            desc = IMAGE_REQUEST if _is_image_placeholder(content) else content
            return f"[图片:{ref}] {desc}" if ref else desc

        if msg_type == "link":
            url = msg.link_msg.url if msg.link_msg else ""
            if url:
                crawled = await link_reader.read(url)
                desc = content or "请分析这个链接"
                return f"{desc}\n\n[链接内容]\n{crawled}" if crawled else f"{desc} {url}"
            return content or None

        if msg_type == "file":
            resource = msg.file_msg.resource if msg.file_msg else None
            ref = resource.ref if resource else ""
            if ref:
                desc = FILE_REQUEST if _is_file_placeholder(content) else content
                return f"[文件:{ref}] {desc}"
            return content or None

        if msg_type == "video":
            return f"[视频消息] {content}" if content else "[视频消息]"

        if msg.refer_msg:
            quoted = msg.refer_msg
            q_type = quoted.msg_type
            q_content = quoted.content

            def _get_ref(sub_msg) -> str:
                if not sub_msg or not sub_msg.resource:
                    return ""
                return sub_msg.resource.ref

            if q_type == "image":
                ref = _get_ref(quoted.image_msg)
                suffix = f"[引用图片:{ref}]" if ref else "[引用图片]"
            elif q_type == "video":
                ref = _get_ref(quoted.video_msg)
                suffix = f"[引用视频:{ref}]" if ref else "[引用视频]"
            elif q_type == "file":
                ref = _get_ref(quoted.file_msg)
                suffix = f"[引用文件:{ref}]" if ref else "[引用文件]"
            else:
                suffix = f"[引用{q_type}内容]: {q_content}"
            return f"{content}\n\n{suffix}" if content else suffix

        return content or None

    async def _send_reply(self, receiver: str, content: str, at_user: str, reply_msg_id: str = "") -> bool:
        """通过 Server 发送最终回复，返回是否发出去了"""
        try:
            ok = await server_client.send_text(receiver, content, at_user, reply_msg_id)
            if not ok:
                LOG.error("发送回复失败: receiver=%s", receiver)
            return ok
        except Exception as e:
            LOG.exception("发送回复异常: %s", e)
            return False

    @staticmethod
    def _extract_first_link(text: str) -> Optional[str]:
        match = re.search(r"https?://[^\s]+", text)
        return match.group() if match else None


# 全局单例
_agent_loop: Optional[AgentLoop] = None


def get_agent_loop() -> AgentLoop:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = AgentLoop()
    return _agent_loop
