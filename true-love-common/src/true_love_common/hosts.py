# -*- coding: utf-8 -*-
"""
各服务的地址

所有机器共用这一份，按机器人区分。机器人的标识是 base 所在机器的机器名（小写），也就是消息里的 bot_id。
通信方向约定：base 只和 server 通信，server 和 base、AI 通信；拿对方给的媒体 URL 下载不算通信。

开发环境（APP_ENV 不是 prod）里 server 和 AI 跑在本机，base 用 m8s 那台。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class BotHosts:
    base: str
    server: str


AI_HOST = "http://h-ser:8088"

# AI 不在处理某条消息时（如启动、关闭通知）发往这个机器人的 server
AI_NOTICE_BOT = "win10-m8s"

BOTS: dict[str, BotHosts] = {
    "win10-m8s": BotHosts(base="http://h-m8s:5000", server="http://h-m8s:8088"),
    "win11-ser": BotHosts(base="http://h-ser:5000", server="http://h-ser:8089"),
    "gcp-win": BotHosts(base="http://gcp-win:5000", server="http://gcp-win:8088"),
}

DEV_AI_HOST = "http://localhost:8079"
DEV_SERVER_HOST = "http://localhost:8078"
DEV_BOT_ID = "win10-m8s"


def is_dev() -> bool:
    """server 和 AI 用：APP_ENV 不是 prod 时读 config-dev.yaml，地址也用开发环境的"""
    return os.environ.get("APP_ENV", "") != "prod"


def machine_bot_id() -> str:
    """server 用：这台 server 服务的机器人。容器里读不到宿主机名，由 compose 传 BOT_ID；开发环境固定用 m8s"""
    bot_id = os.environ.get("BOT_ID") or (DEV_BOT_ID if is_dev() else socket.gethostname())
    return bot_id.lower()


def bot_hosts(bot_id: str) -> BotHosts:
    """机器人对应的 base 和 server 地址（生产环境）"""
    hosts = BOTS.get(bot_id.lower())
    if hosts is None:
        raise KeyError(f"未登记的机器人: {bot_id!r}，在 true_love_common/hosts.py 的 BOTS 里加一行")
    return hosts


def server_host(bot_id: str) -> str:
    """机器人对应的 server 地址，开发环境是本机的 server"""
    return DEV_SERVER_HOST if is_dev() else bot_hosts(bot_id).server


def ai_host() -> str:
    return DEV_AI_HOST if is_dev() else AI_HOST
