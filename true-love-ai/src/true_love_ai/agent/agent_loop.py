# -*- coding: utf-8 -*-
"""
Agent Loop

核心 Agent 执行引擎：接收消息 → 构建上下文 → LLM + tools 循环 → 发送回复

流程：
    收到 msg
      → 从 AI 本地 DB 获取 user_ctx
      → 获取 session history
      → build prompt
      → LLM (带 tools)
      → if tool_calls: 执行 skill → append 结果 → 继续循环
      → if text answer: 通过 Server /action/send 发送 → 保存 session
"""

import asyncio
import json
import logging
import re
from typing import Optional

from true_love_common.chat_msg import ChatMsg
from true_love_ai.core.session import get_session_manager
from true_love_ai.llm.router import get_llm_router
from true_love_ai.memory.memory_manager import get_user_context
from true_love_ai.agent import link_reader, outcome, skill_registry
from true_love_ai.agent.outcome import Ending, Outcome

LOG = logging.getLogger("AgentLoop")

# 单次会话最大 tool 调用轮次（防止死循环）
MAX_TOOL_ITERATIONS = 6
# 单个 skill 执行超时（秒）
SKILL_TIMEOUT_SECONDS = 300
# 自动触发时附在这条消息后面（只给这一次调用，不进会话历史），让模型没话说时可以不回
AUTO_REPLY_RULE = (
    "（系统提示：上面这条没人叫你，是你在群里自己刷到的，群里的人并不期待你说话。"
    f"默认只回复 {outcome.SKIP_MARKER}。"
    "只有你真的能说出让人眼前一亮的东西才开口：好笑的吐槽或梗、对方可能不知道的有用信息、指出明显的错误或谣言。"
    "复述或描述内容（比如“图上是一个人在健身”“这张图讲的是…”）、泛泛的建议和提醒、为了接话而接话，都不算，"
    f"这些情况一律只回复 {outcome.SKIP_MARKER}，不要加别的字。）"
)
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


class AgentLoop:
    """Agent 执行引擎（单例使用）"""

    def __init__(self):
        self.llm_router = get_llm_router()
        self.session_manager = get_session_manager()

    async def run(self, msg: ChatMsg) -> None:
        """处理一条消息：_think 得出结局，finish 统一收尾"""
        # 会话按机器人隔离：同名的群在不同的号里是不同的会话；用户画像跟着会话走（每个群一份、私聊一份）
        session_chat = msg.chat_id if msg.is_group else msg.sender_id
        session_id = f"{msg.bot_id}:{session_chat}"
        receiver, at_user, _ = _route(msg)
        auto = is_auto_triggered(msg)

        LOG.info("AgentLoop.run: bot_id=%s platform=%s sender_id=%s sender_name=%s session=%s type=%s auto=%s",
                 msg.bot_id, msg.platform, msg.sender_id, msg.sender_name or msg.sender_id, session_id,
                 msg.msg_type, auto)

        # 没人叫它、自动接话时不发技能的"正在…"提示，免得群里一张图刷两条
        ending, session = await self._think(msg, session_id, session_chat, receiver, at_user,
                                            notify=not auto, auto=auto)
        await self.finish(msg, ending, session)

    async def _think(self, msg: ChatMsg, session_id: str, session_chat: str, receiver: str, at_user: str,
                     notify: bool, auto: bool):
        """跑一遍 LLM + 技能循环，返回 (结局, 会话)；不发任何消息，发不发、发什么由 finish 统一决定"""
        platform, bot_id, sender_id = msg.platform, msg.bot_id, msg.sender_id
        sender_name = msg.sender_name or sender_id
        is_group = msg.is_group

        # 构建用户侧消息内容
        user_content = self._build_user_content(msg)
        if not user_content:
            return Ending(Outcome.UNREADABLE), None

        # 获取用户画像并注入 session
        user_ctx = get_user_context(session_id, sender_id)
        # 人设按"这个机器人里的这个群或人"选，名字用 base 带来的昵称
        session = self.session_manager.get_or_create(session_id, user_ctx=user_ctx, bot_id=bot_id,
                                                     chat=session_chat, bot_name=msg.bot_name)
        session.add_message("user", user_content)

        # 获取当前用户在这里有权限使用的 tools（权限点按平台、号、群、人匹配）
        access = {"platform": platform, "bot_id": bot_id, "sender_id": sender_id, "is_group": is_group,
                  "chat": msg.chat_id if is_group else ""}
        tools = skill_registry.get_all_tool_schemas(access)

        # 开始 Agent Loop
        messages = session.get_messages_for_llm(access)
        if auto:
            # 只告诉这一次调用，不写进会话历史
            messages = [*messages, {"role": "user", "content": AUTO_REPLY_RULE}]
        last_tool_result = ""

        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                result_type, result = await self.llm_router.chat_for_agent(
                    messages=messages,
                    tools=tools,
                )
            except Exception as e:
                LOG.exception("LLM 调用失败 (iteration=%d): %s", iteration, e)
                return Ending(Outcome.LLM_ERROR, detail=repr(e)[:200]), session

            if result_type == "text":
                return outcome.from_model_reply(result, last_tool_result, auto), session

            # result_type == "tool_calls"
            tool_calls = result
            LOG.info("LLM 请求执行 %d 个 tool (iteration=%d)", len(tool_calls), iteration)

            # 1. 把 assistant 的 tool_calls 消息追加到 messages
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # 2. 并行执行所有 tool，把结果追加到 messages
            tool_results = await asyncio.gather(*[
                self._execute_tool(tc, session_id, sender_id, sender_name, is_group, receiver, at_user, platform,
                                   bot_id, notify=notify)
                for tc in tool_calls
            ])
            for tc, tool_result in zip(tool_calls, tool_results):
                last_tool_result = tool_result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        LOG.warning("AgentLoop 超过最大迭代次数 (%d)", MAX_TOOL_ITERATIONS)
        return Ending(Outcome.TOO_MANY_ROUNDS), session

    async def finish(self, msg: ChatMsg, ending: Ending, session=None) -> None:
        """
        一条消息的唯一收口：按结局决定发不发、发什么，打一行"回复结局"日志

        发出去的话记进会话历史；没发的（不回、自动触发时的各种兜底）不记。
        """
        auto = is_auto_triggered(msg)
        text = outcome.text_to_send(ending, auto)
        if text:
            if session is not None:
                session.add_message("assistant", text)
            receiver, at_user, reply_msg_id = _route(msg)
            await self._send_reply(receiver, text, at_user, reply_msg_id)
        outcome.log_outcome(ending, bot_id=msg.bot_id, chat_id=msg.chat_id, sender_id=msg.sender_id,
                            msg_type=msg.msg_type, auto=auto, sent=bool(text))

    def _build_user_content(self, msg: ChatMsg) -> Optional[str]:
        """把各类消息类型转换为 LLM 可理解的文本"""
        msg_type = msg.msg_type
        content = _clean_content(msg.content, msg.mention)

        if msg_type == "text":
            url = self._extract_first_link(content)
            if url:
                crawled = self._crawl_content(url)
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
                crawled = self._crawl_content(url)
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

    async def _execute_tool(
            self,
            tool_call: dict,
            session_id: str,
            sender_id: str,
            sender_name: str,
            is_group: bool,
            receiver: str,
            at_user: str,
            platform: str,
            bot_id: str,
            notify: bool = True,
    ) -> str:
        """执行单个 tool，返回结果字符串；notify 为 False 时不发技能的预通知"""
        name = tool_call["name"]
        args = tool_call["arguments"]
        LOG.info("执行 tool: %s, args=%s", name, str(args)[:200])

        ctx = {
            "session_id": session_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "is_group": is_group,
            "receiver": receiver,
            "at_user": at_user,
            "platform": platform,
            "bot_id": bot_id,
            # 权限点按群匹配用；私聊为空
            "chat": receiver if is_group else "",
        }

        try:
            notify_msg = skill_registry.get_notify(name) if notify else None
            if notify_msg:
                import random
                from true_love_ai.agent.server_client import send_text
                msg = random.choice(notify_msg) if isinstance(notify_msg, list) else notify_msg
                await send_text(receiver, msg, at_user)

            result = await asyncio.wait_for(
                skill_registry.execute(name, args, ctx),
                timeout=SKILL_TIMEOUT_SECONDS,
            )
            LOG.info("tool %s 执行结果: %s", name, str(result)[:200])
            return str(result)
        except asyncio.TimeoutError:
            LOG.error("tool %s 执行超时 (%ds)", name, SKILL_TIMEOUT_SECONDS)
            return f"[执行超时] 技能 {name} 处理时间过长，请稍后重试"
        except Exception as e:
            from true_love_ai.agent.skills.permission import PermissionDenied
            if isinstance(e, PermissionDenied):
                return str(e)
            LOG.exception("tool %s 执行异常: %s", name, e)
            return f"[执行失败] {e}"

    async def _send_reply(self, receiver: str, content: str, at_user: str, reply_msg_id: str = "") -> None:
        """通过 Server 发送最终回复"""
        from true_love_ai.agent.server_client import send_text
        try:
            ok = await send_text(receiver, content, at_user, reply_msg_id)
            if not ok:
                LOG.error("发送回复失败: receiver=%s", receiver)
        except Exception as e:
            LOG.exception("发送回复异常: %s", e)

    @staticmethod
    def _extract_first_link(text: str) -> Optional[str]:
        match = re.search(r"https?://[^\s]+", text)
        return match.group() if match else None

    @staticmethod
    def _crawl_content(url: str) -> str:
        return link_reader.read(url)


# 全局单例
_agent_loop: Optional[AgentLoop] = None


def get_agent_loop() -> AgentLoop:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = AgentLoop()
    return _agent_loop
