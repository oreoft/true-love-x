# -*- coding: utf-8 -*-
"""
Skill 权限检查

内置技能和安装的技能都按 skill_access_service 里的权限点判断，所有机器人共用一份，在 tl-admin 改。
ctx 里要有 platform、bot_id、sender_id、is_group、chat（群名，私聊为空）。
"""
from true_love_ai.memory import skill_access_service


class PermissionDenied(Exception):
    pass


def check_permission(skill_name: str, ctx: dict) -> bool:
    """返回 True 表示允许"""
    return skill_access_service.allowed(skill_name, ctx)


def require_permission(skill_name: str, ctx: dict) -> None:
    """检查权限，无权限时抛 PermissionDenied。"""
    if not check_permission(skill_name, ctx):
        raise PermissionDenied("诶嘿~这个功能你没有权限使用哦~")
