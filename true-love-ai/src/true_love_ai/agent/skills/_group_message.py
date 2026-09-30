# -*- coding: utf-8 -*-
"""群消息取数 helper（内部使用，不注册为 skill）"""

from true_love_ai.agent.server_client import query_history


async def fetch_group_messages(chat_id: str, limit: int,
                               sender_id: str = "", sender_name: str = "") -> list[dict]:
    """统一取当前机器人的群消息，sender_id / sender_name 均为可选过滤条件。"""
    return await query_history(
        chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        limit=limit,
    )
