# -*- coding: utf-8 -*-
"""
True Love Server - tl-server，多个机器人共用的服务端

微信机器人后端服务，处理消息和定时任务。
"""

__version__ = "0.2.0"

from .core import Config
from .models import ChatMsg
from .api import create_app

__all__ = [
    "__version__",
    "Config",
    "ChatMsg",
    "create_app",
]
