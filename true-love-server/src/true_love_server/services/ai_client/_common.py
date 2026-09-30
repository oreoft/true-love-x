# -*- coding: utf-8 -*-
"""调 AI 时共用的 token"""

from ...core import Config


def token() -> str:
    """server 调 AI 用配置里的第一个 token"""
    tokens = Config().HTTP_TOKEN or []
    return tokens[0] if tokens else ""
