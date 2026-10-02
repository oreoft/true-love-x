# -*- coding: utf-8 -*-
"""
server 和 AI 的地址

所有机器人共用一个 server 和一个 AI，地址固定写在这里。base 的地址不写死：
base 每次调 server 时带上自己的回调地址，server 记在 bots 表里。
通信方向约定：base 只和 server 通信，server 和 base、AI 通信；拿对方给的媒体 URL 下载不算通信。

开发环境（APP_ENV 不是 prod）里 server 和 AI 跑在本机。base 只跑在部署机上，始终用生产环境的 server。
"""

from __future__ import annotations

import os

SERVER_HOST = "http://h-m8s:8088"
AI_HOST = "http://ser-docker:8088"

DEV_SERVER_HOST = "http://localhost:8078"
DEV_AI_HOST = "http://localhost:8079"


def is_dev() -> bool:
    """server 和 AI 用：APP_ENV 不是 prod 时读 config-dev.yaml，地址也用开发环境的"""
    return os.environ.get("APP_ENV", "") != "prod"


def server_host() -> str:
    return DEV_SERVER_HOST if is_dev() else SERVER_HOST


def ai_host() -> str:
    return DEV_AI_HOST if is_dev() else AI_HOST
