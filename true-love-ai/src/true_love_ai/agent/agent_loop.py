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
from true_love_ai.agent import link_reader, skill_registry

LOG = logging.getLogger("AgentLoop")

# 单次会话最大 tool 调用轮次（防止死循环）
MAX_TOOL_ITERATIONS = 6
# 单个 skill 执行超时（秒）
SKILL_TIMEOUT_SECONDS = 300
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
        platform = msg.platform
        bot_id = msg.bot_id
        sender_id = msg.sender_id
        sender_name = msg.sender_name or sender_id
        chat_id = msg.chat_id
        is_group = msg.is_group
        msg_type = msg.msg_type

        # 会话按机器人隔离：同名的群在不同的号里是不同的会话；用户画像跟着会话走（每个群一份、私聊一份）
        _session_base = chat_id if is_group else sender_id
        session_id = f"{bot_id}:{_session_base}"
        at_user = sender_id if is_group else ""
        receiver = chat_id if is_group else sender_id
        # 群里的回复带上原消息，base 按设置 @、拍一拍或引用对方
        reply_msg_id = msg.msg_id if is_group else ""

        LOG.info("AgentLoop.run: bot_id=%s platform=%s sender_id=%s sender_name=%s session=%s type=%s",
                 bot_id, platform, sender_id, sender_name, session_id, msg_type)

        # 构建用户侧消息内容
        user_content = self._build_user_content(msg)
        if not user_content:
            LOG.warning("无法解析消息内容，跳过: type=%s", msg_type)
            await self._send_reply(receiver, "抱歉，这种消息我暂时还不太看得懂呢~", at_user, reply_msg_id)
            return

        # 获取用户画像并注入 session
        user_ctx = get_user_context(session_id, sender_id)
        # 人设按"这个机器人里的这个群或人"选，名字用 base 带来的昵称
        session = self.session_manager.get_or_create(session_id, user_ctx=user_ctx, bot_id=bot_id,
                                                     chat=_session_base, bot_name=msg.bot_name)
        session.add_message("user", user_content)

        # 获取当前用户在这里有权限使用的 tools（权限点按平台、号、群、人匹配）
        access = {"platform": platform, "bot_id": bot_id, "sender_id": sender_id, "is_group": is_group,
                  "chat": chat_id if is_group else ""}
        tools = skill_registry.get_all_tool_schemas(access)

        # 开始 Agent Loop
        messages = session.get_messages_for_llm(access)
        reply = None
        last_tool_result = ""

        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                result_type, result = await self.llm_router.chat_for_agent(
                    messages=messages,
                    tools=tools,
                )
            except Exception as e:
                LOG.exception("LLM 调用失败 (iteration=%d): %s", iteration, e)
                reply = "呜呜~出了点小状况，稍后再试试吧~"
                break

            if result_type == "text":
                reply = result
                # 模型调完技能偶尔只回空文本，用户就什么也收不到；这时把技能自己的结果发出去
                if not (reply or "").strip():
                    LOG.warning("LLM 返回空回复 (iteration=%d)，%s", iteration,
                                "改发技能结果" if last_tool_result else "改发兜底回复")
                    reply = last_tool_result or "嗯嗯，收到啦~"
                break

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
                                   bot_id)
                for tc in tool_calls
            ])
            for tc, tool_result in zip(tool_calls, tool_results):
                last_tool_result = tool_result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })
        else:
            # 超出最大轮次
            LOG.warning("AgentLoop 超过最大迭代次数 (%d)", MAX_TOOL_ITERATIONS)
            reply = "处理超时了，稍后再试试吧~"

        if reply:
            session.add_message("assistant", reply)
            await self._send_reply(receiver, reply, at_user, reply_msg_id)

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
    ) -> str:
        """执行单个 tool，返回结果字符串"""
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
            notify_msg = skill_registry.get_notify(name)
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
