# -*- coding: utf-8 -*-
"""发言分析 Skill（从 Server 拉取历史，AI 侧生成分析报告）"""
import logging
import random

from true_love_ai.agent.skill_registry import register_skill

LOG = logging.getLogger("AnalyzeSpeechSkill")


@register_skill({
    "type": "function",
    "function": {
        "name": "analyze_speech",
        "description": (
                "分析群成员的发言特点、性格或意图，基于群内历史发言记录生成报告。"
                "当用户说'分析我的发言'、'分析xxx的性格'、'帮我看看xxx的发言'时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                            "要分析的目标昵称。"
                            "如果分析用户自身，返回 'self'。"
                            "如果分析其他人，返回对方昵称。"
                    )
                }
            },
            "required": ["target"]
        }
    }
})
async def analyze_speech(params: dict, ctx: dict) -> str:
    target = params.get("target", "self")
    sender_id = ctx.get("sender_id", "")
    sender_name = ctx.get("sender_name", sender_id)
    session_id = ctx.get("session_id", "")
    receiver = ctx.get("receiver", "")
    at_user = ctx.get("at_user", "")

    is_self = target.strip().lower() == "self"
    target_name = target.strip().lstrip("@").strip()
    display_name = sender_name if is_self else target_name

    # 发安抚语（通过 Server 发送）
    wait_msgs = [
        f"正在戴上老花镜，翻阅[{display_name}]在群里所有的发言，请稍等哈...",
        f"收到！正在在群里扒[{display_name}]的黑历史，稍微等我一下哦~",
        f"正在检索[{display_name}]最近的发言数据，看我稍后怎么评价...",
    ]
    from true_love_ai.agent.server_client import send_text as _send
    if not await _send(receiver, random.choice(wait_msgs), at_user):
        LOG.warning("发言分析的安抚语没发出去: receiver=%s", receiver)

    # 从 Server 查询历史
    from true_love_ai.agent.skills._group_message import fetch_group_messages
    if is_self:
        history = await fetch_group_messages(receiver, limit=100, sender_id=sender_id)
    else:
        history = await fetch_group_messages(receiver, limit=100, sender_name=target_name)

    if not history:
        return f"我没能获取到[{display_name}]在群里以前的发言记录哦，所以我没有足够的信息来分析捏~"

    speech_history_text = "\n".join([f"[{item['created_at']}] {item['content']}" for item in history])
    LOG.info("开始分析 [%s]，共 %d 条记录", display_name, len(history))

    # 调用 LLM 生成分析报告
    from true_love_ai.llm.analyze_speech_prompt import get_analyze_system_prompt
    from true_love_ai.llm.router import get_llm_router
    metadata = {
        "target": f"分析群成员 {display_name} 的发言特点、性格或意图",
        "target_name": display_name,
        "is_self": is_self,
    }
    analyze_system_prompt = get_analyze_system_prompt(speech_history_text, metadata)
    answer = await get_llm_router().chat(
        messages=[
            {"role": "system", "content": analyze_system_prompt},
            {"role": "user", "content": metadata["target"]},
        ]
    )

    # 后台提取记忆并写入 AI 本地 DB
    from true_love_ai.core.background import spawn
    spawn(_extract_and_save_memory(answer, display_name, session_id), "analyze_speech_memory")

    return answer


async def _extract_and_save_memory(analysis_text: str, sender_id: str, group_id: str) -> None:
    """后台从分析报告中提取结构化记忆并写入 AI SQLite；异常由 spawn 记日志"""
    from true_love_ai.services.chat_service import ChatService
    facts = await ChatService().extract_memory_facts(analysis_text, sender_id)
    if facts:
        from true_love_ai.memory.memory_manager import upsert_user_memory
        count = upsert_user_memory(group_id, sender_id, facts, source="analyze_speech")
        if count < len(facts):
            LOG.warning("记忆只写入了 %d/%d 条: sender_id=%s, group=%s", count, len(facts), sender_id, group_id)
        else:
            LOG.info("记忆写入完成: %d 条, sender_id=%s, group=%s", count, sender_id, group_id)
