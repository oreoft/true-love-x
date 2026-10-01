# -*- coding: utf-8 -*-
"""
后台任务

asyncio 只弱引用任务，不存起来的话可能跑到一半被回收；任务抛的异常没人 await 也就没人看见。
起后台任务一律用 spawn：引用存到模块级的集合里，结束时有异常就记堆栈。
"""
import asyncio
import logging
from typing import Coroutine

LOG = logging.getLogger("Background")

# 还没结束的后台任务，结束时自己移除
_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine, name: str) -> asyncio.Task:
    """在当前事件循环里起一个后台任务；不在事件循环里时抛 RuntimeError（coro 会被关掉，不留警告）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        raise
    task = loop.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        LOG.warning("后台任务 %s 被取消", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        LOG.error("后台任务 %s 异常退出: %s", task.get_name(), exc, exc_info=exc)
