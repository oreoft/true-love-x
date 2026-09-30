# -*- coding: utf-8 -*-
"""
AI Agent Skills

这个文件夹里的每个模块在 import 时用 @register_skill 注册自己。启动时自动扫描加载，
新加技能只要放一个文件进来；下划线开头的是共用的小工具，不扫。
"""
import importlib
import pkgutil

_loaded = False


def ensure_skills_loaded():
    """确保所有 skills 已加载注册（幂等）"""
    global _loaded
    if _loaded:
        return
    _loaded = True

    for module in pkgutil.iter_modules(__path__):
        if not module.name.startswith("_"):
            importlib.import_module(f"{__name__}.{module.name}")
