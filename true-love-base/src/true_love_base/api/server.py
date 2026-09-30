"""HTTP server bootstrap for true-love-base."""

import logging
from threading import Thread
from typing import TYPE_CHECKING, Optional

import uvicorn

from true_love_base.api.app import app

if TYPE_CHECKING:
    from true_love_base.services.robot import Robot

LOG = logging.getLogger("Server")

# base 的 HTTP 端口，回调地址里也用它
HTTP_PORT = 5000

# 全局 Robot 实例
_robot: Optional["Robot"] = None


def get_robot() -> Optional["Robot"]:
    """获取 Robot 实例"""
    return _robot


def enable_http(robot: "Robot", host: str = "0.0.0.0", port: int = HTTP_PORT) -> None:
    """
    启动 HTTP 服务（使用 Uvicorn）

    Args:
        robot: Robot 实例
        host: 绑定地址
        port: 端口
    """
    global _robot
    _robot = robot

    def run_server() -> None:
        """运行 Uvicorn 服务器"""
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )

    t = Thread(
        target=run_server,
        name="HttpServer",
        daemon=True,  # 守护线程，主线程退出时自动结束
    )
    t.start()

    LOG.info("HTTP server (Uvicorn) started on %s:%s", host, port)
