# -*- coding: utf-8 -*-
"""群聊整体分析 Skill（拉取全群历史，AI 侧按问题生成分析）"""
import logging

from true_love_ai.agent.skill_registry import register_skill

LOG = logging.getLogger("AnalyzeGroupSkill")


@register_skill({
    "type": "function",
    "notify": [
        "正在翻阅群里最近的聊天记录，稍等我一下哈~",
        "收到！正在扒群聊历史，马上给你分析...",
        "正在检索群内消息，看我等下怎么说~",
        "好嘞，正在读取群聊记录，稍微等我一会儿哦",
    ],
    "function": {
        "name": "analyze_group",
        "description": (
            "基于群内全员聊天记录，回答关于整个群体的分析问题。"
            "适用场景：总结群里最近在聊什么、分析群里谁最搞笑、谁最有可能给我钱、"
            "群里最活跃的人是谁、大家对某件事的态度如何等需要综合所有人发言的问题。"
            "注意：如果只是分析某一个人的性格或发言特点，请用 analyze_speech 而不是此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "要分析的问题，直接描述分析目标。"
                        "例如：'大家最近在聊什么'、'谁最搞笑'、'谁最有可能自愿给我五十块'。"
                    )
                }
            },
            "required": ["question"]
        }
    }
})
async def analyze_group(params: dict, ctx: dict) -> str:
    question = params.get("question", "").strip()
    receiver = ctx.get("receiver", "")

    from true_love_ai.agent.skills._group_message import fetch_group_messages
    history = await fetch_group_messages(receiver, limit=500)

    if not history:
        return "我没能获取到群里最近的聊天记录，没有足够的信息来分析捏~"

    chat_history_text = "\n".join(
        [f"[{item['created_at']}][{item['sender_name']}] {item['content']}" for item in history]
    )
    LOG.info("开始群聊分析，问题: [%s]，共 %d 条记录", question, len(history))

    from true_love_ai.llm.analyze_group_prompt import get_analyze_group_system_prompt
    from true_love_ai.llm.router import get_llm_router
    system_prompt = get_analyze_group_system_prompt(chat_history_text, question)
    answer = await get_llm_router().chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]
    )

    return answer
