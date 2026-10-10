#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
True Love AI - AI service module.
tl-ai：AI 服务
"""

__version__ = "0.2.0"

import os as _os

# pydantic_ai 第一次运行时往 stdout 打一个推广 Logfire 的横幅，会混进 JSON 日志
_os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
