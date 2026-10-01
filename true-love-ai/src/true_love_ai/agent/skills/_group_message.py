# -*- coding: utf-8 -*-
"""群消息取数 helper（内部使用，不注册为 skill）"""

from true_love_ai.agent.server_client import ServerCallFailed, query_history
from true_love_ai.agent.skill_registry import SkillFailed

# 拉群消息失败时回给模型的话；没有记录是正常结果，不走这里
FETCH_FAILED_TEXT = "呜呜~群聊记录没拉下来，稍后再试试吧~"


async def fetch_group_messages(chat_id: str, limit: int,
                               sender_id: str = "", sender_name: str = "") -> list[dict]:
    """统一取当前机器人的群消息，sender_id / sender_name 均为可选过滤条件；拉取失败抛 SkillFailed"""
    try:
        return await query_history(
            chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            limit=limit,
        )
    except ServerCallFailed as e:
        raise SkillFailed(FETCH_FAILED_TEXT) from e
