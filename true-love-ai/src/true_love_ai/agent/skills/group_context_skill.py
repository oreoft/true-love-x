# -*- coding: utf-8 -*-
"""
群聊上下文检索 Skill

轻量级工具，供 Agent 在检测到隐式指代时主动拉取近期群消息，
自行解析指代后再回答，不做额外 LLM 分析调用。
"""
import logging

from true_love_ai.agent.skill_registry import register_skill

LOG = logging.getLogger("GroupContextSkill")

_DEFAULT_LIMIT = 60
_KEYWORD_FETCH = 200   # 按关键词过滤时先多拉，再筛选
_KEYWORD_RETURN = 30   # 关键词命中后最多返回条数
_MAX_LIMIT = 100


@register_skill({
    "type": "function",
    "function": {
        "name": "fetch_group_context",
        "description": (
            "拉取本群近期聊天记录，用于解决用户消息中的隐式指代问题。"
            "当用户使用【这个】【那个】【他说的】【之前提到的】【刚才讲的】等指代词，"
            "且当前对话上下文中找不到对应的指代物时，主动调用此工具获取群聊记录后再回答，"
            "不要猜测或编造。"
            "也可用于用户主动要求查看最近群聊内容的场景。"
            "注意：仅适用于群聊场景，私聊中不可用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": f"拉取最近消息条数，默认 {_DEFAULT_LIMIT}，最大 {_MAX_LIMIT}。指代不明时用默认值即可。",
                },
                "keyword": {
                    "type": "string",
                    "description": (
                        "可选。若用户的指代词有明确主题（如【那个方案】【xxx链接】），"
                        "填入关键词可缩小检索范围，返回包含该词的近期消息。"
                        "不确定时留空，直接拉取最近记录。"
                    ),
                },
            },
        },
    },
})
async def fetch_group_context(params: dict, ctx: dict) -> str:
    if not ctx.get("is_group"):
        return "[fetch_group_context] 此工具仅适用于群聊场景"

    receiver = ctx.get("receiver", "")
    keyword = (params.get("keyword") or "").strip()
    limit = min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)

    from true_love_ai.agent.skills._group_message import fetch_group_messages

    if keyword:
        raw = await fetch_group_messages(receiver, limit=_KEYWORD_FETCH)
        matched = [m for m in raw if keyword in m.get("content", "")]
        messages = matched[-_KEYWORD_RETURN:]
        LOG.info("fetch_group_context: keyword=%s, fetched=%d, matched=%d", keyword, len(raw), len(matched))
    else:
        messages = await fetch_group_messages(receiver, limit=limit)
        LOG.info("fetch_group_context: no keyword, fetched=%d", len(messages))

    if not messages:
        return "未找到相关群聊记录"

    lines = [f"[{m['created_at']}][{m['sender_name']}] {m['content']}" for m in messages]
    header = f"以下是本群最近 {len(lines)} 条聊天记录（{'含关键词: ' + keyword if keyword else '无过滤'}）：\n"
    return header + "\n".join(lines)
