# -*- coding: utf-8 -*-
"""
Main Entry Point - 主入口

启动 tl-server。
"""

import asyncio
import signal
import logging

import uvicorn

from .services import base_client, bot_registry
from .api import create_app
from .core import Config
from .core.db_engine import init_platform_db

LOG = logging.getLogger("Main")
config = Config()


def _run_async(coro):
    """在当前线程创建独立事件循环执行协程，用于 event loop 启动前/信号处理中。"""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    except Exception:
        pass
    finally:
        loop.close()


def notice_master():
    """启动通知和信号处理：从默认机器人发给它的管理员，管理员是谁由 base 决定"""
    _run_async(base_client.send_to_master("", "tl-server 启动成功"))

    def handler(sig, frame):
        """退出前清理环境"""
        _run_async(base_client.send_to_master("", "tl-server 正在关闭..."))
        exit(0)

    signal.signal(signal.SIGINT, handler)


def main():
    """主函数"""
    init_platform_db()

    # 启动调度器，再打开登记过的机器人的库，给每个机器人挂上它的提醒和定时任务
    from .services.scheduler_service import add_bot_store, start_scheduler
    start_scheduler()
    bot_registry.on_new_bot(add_bot_store)
    bot_registry.open_all()

    # 通知 master
    notice_master()

    # 启动应用
    app = create_app()

    # 获取 HTTP 配置
    http_config = config.HTTP or {}
    host = http_config.get("host", "0.0.0.0")
    port = http_config.get("port", 8088)

    LOG.info("启动 FastAPI 服务: %s:%s", host, port)

    # 启动 FastAPI（使用 uvicorn）
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,  # 使用自定义日志中间件，关闭 uvicorn 访问日志
    )


if __name__ == '__main__':
    main()

