# -*- coding: utf-8 -*-
"""
Auto AI Limit - 群里自动交给 AI 的疲劳限制

同一个机器人、同一个群、同一种消息（链接、PDF、图片、笔记），最近 seconds 秒内最多自动交给 AI count 条，
条数和秒数在后台按机器人、按类型设（见 bot_settings.AUTO_AI_LIMITS）。超出的只入库，不回复也不提示。
@ 机器人和私聊不受它管，那些走 ai_rate_limit。

计数只在内存里，server 重启就清零。
"""

import threading
import time
from collections import deque

_hits: dict[tuple[str, str, str], deque] = {}
_lock = threading.Lock()


def allow(bot_id: str, chat_id: str, kind: str, count: int, seconds: int, now: float | None = None) -> bool:
    """记一次自动交给 AI，没超限返回 True"""
    now = time.monotonic() if now is None else now
    key = (bot_id, chat_id, kind)
    with _lock:
        hits = _hits.setdefault(key, deque())
        while hits and now - hits[0] >= seconds:
            hits.popleft()
        if len(hits) >= count:
            return False
        hits.append(now)
        return True


def reset() -> None:
    """清空计数，测试用"""
    with _lock:
        _hits.clear()
