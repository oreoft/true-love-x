# -*- coding: utf-8 -*-
"""
AI Rate Limit - 限制同一个人找 AI 的频率

同一个机器人、同一个会话里的同一个人，从第一次找 AI 起的 seconds 秒内最多交给 AI count 次，
条数和秒数在后台按机器人设（bot_settings.AI_RATE_LIMIT），默认 180 秒 6 次。
超出后第一次回一句"太频繁了"，之后只入库、不交给 AI，到期后重新计数。
只算要交给 AI 的消息（@ 机器人、私聊，以及开了开关后群里自动交给 AI 的链接、PDF、图片、笔记），群里的普通聊天不计数。
也用来拦住两个机器人在同一个群里互相 @、来回回复：实测一轮约 27 秒，默认设置下 3 分钟内会到第 7 次。

计数只在内存里，server 重启就清零。
"""

import threading
import time
from dataclasses import dataclass

LIMIT = 6
WINDOW_SECONDS = 180
BUSY_REPLY = "呜哇~你问得太快啦，本酱的小脑袋要冒烟了，先让我歇一会儿捏~ (｡•́︿•̀｡)"

ALLOW = "allow"
NOTIFY = "notify"
DROP = "drop"


@dataclass
class _Window:
    started_at: float
    seconds: float
    count: int = 0


_windows: dict[tuple[str, str, str], _Window] = {}
_lock = threading.Lock()


def check(bot_id: str, chat_id: str, sender_id: str, now: float | None = None,
          limit: int = LIMIT, seconds: float = WINDOW_SECONDS) -> str:
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
        if window is None or now - window.started_at >= window.seconds:
            window = _windows[key] = _Window(started_at=now, seconds=seconds)
            # 顺手清掉过期的计数，免得内存一直涨
            for other in [k for k, w in _windows.items() if now - w.started_at >= w.seconds]:
                del _windows[other]
        window.count += 1
        if window.count <= limit:
            return ALLOW
        return NOTIFY if window.count == limit + 1 else DROP


def reset() -> None:
    """清空计数，测试用"""
    with _lock:
        _windows.clear()
