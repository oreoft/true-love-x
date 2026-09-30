# -*- coding: utf-8 -*-
"""
AI Rate Limit - 限制同一个人找 AI 的频率

同一个机器人、同一个会话里的同一个人，从第一次找 AI 起的 1 分钟内最多交给 AI 5 次。
超出后第一次回一句"太频繁了"，之后只入库、不交给 AI，1 分钟到期后重新计数。
只算要交给 AI 的消息（@ 机器人或私聊），群里的普通聊天不计数。
也用来拦住两个机器人在同一个群里互相 @、来回回复。

计数只在内存里，server 重启就清零。
"""

import threading
import time
from dataclasses import dataclass

LIMIT = 5
WINDOW_SECONDS = 60
BUSY_REPLY = "你问的太频繁了，我暂时要休息一会"

ALLOW = "allow"
NOTIFY = "notify"
DROP = "drop"


@dataclass
class _Window:
    started_at: float
    count: int = 0


_windows: dict[tuple[str, str, str], _Window] = {}
_lock = threading.Lock()


def check(bot_id: str, chat_id: str, sender_id: str, now: float | None = None) -> str:
    """
    记一次找 AI，返回怎么处理：

    - ALLOW：交给 AI
    - NOTIFY：刚超限，回一句 BUSY_REPLY，不交给 AI
    - DROP：已经提醒过，只入库
    """
    now = time.monotonic() if now is None else now
    key = (bot_id, chat_id, sender_id)
    with _lock:
        window = _windows.get(key)
        if window is None or now - window.started_at >= WINDOW_SECONDS:
            window = _windows[key] = _Window(started_at=now)
            # 顺手清掉过期的计数，免得内存一直涨
            for other in [k for k, w in _windows.items() if now - w.started_at >= WINDOW_SECONDS]:
                del _windows[other]
        window.count += 1
        if window.count <= LIMIT:
            return ALLOW
        return NOTIFY if window.count == LIMIT + 1 else DROP


def reset() -> None:
    """清空计数，测试用"""
    with _lock:
        _windows.clear()
