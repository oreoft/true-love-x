# -*- coding: utf-8 -*-
"""
AI 的业务接口

- POST /trigger：把需要 AI 处理的消息交给 AI，AI 立即确认收到，回复之后通过 server 的 /action/* 发回来
- GET  /data/*：定时任务要用的数据（汇率、金价）
"""

import logging

from true_love_common.chat_msg import ChatMsg
from true_love_common.hosts import ai_host
from true_love_common.http.client import get, post_json

from ._common import token

LOG = logging.getLogger("AiBusinessClient")


def trigger(msg: ChatMsg) -> None:
    """把消息交给 AI（同步，放在线程里调）；AI 没接收时抛异常"""
    resp = post_json(f"{ai_host()}/trigger", {"token": token(), "msg": msg.to_dict()}, timeout=(5, 10))
    resp.raise_for_status()
    data = resp.data if isinstance(resp.data, dict) else {}
    if str(data.get("code", 0)) != "0":
        raise RuntimeError(f"AI trigger 返回业务失败: {data}")
    LOG.info("AI trigger 成功: bot_id=%s sender_id=%s", msg.bot_id, msg.sender_id)


def fetch_data(path: str, params: dict = None) -> str:
    """从 AI 的 /data/* 取一段文本；失败时返回空串，不抛异常（任务不依赖它成功）"""
    try:
        resp = get(f"{ai_host()}{path}", params={"token": token(), **(params or {})}, timeout=15)
        resp.raise_for_status()
        return (resp.data or {}).get("data", {}).get("text", "")
    except Exception as e:
        LOG.error("fetch_data %s 失败: %s", path, e)
        return ""
