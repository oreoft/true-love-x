# -*- coding: utf-8 -*-
"""
Skill 权限管理 - 融合规则

规则优先级：
  规则2（代码/DB 声明）> 规则3（tl-admin 里按机器人配的规则）> 规则1（默认开放）

权限格式（list[str]）：
  ["*"]                          — 所有平台所有人
  ["wechat:*"]                   — 微信全员
  ["wechat:admin", "lark:*"]     — 微信特定用户 + 飞书全员
  ["wechat:user1"]               — 单个用户（微信是昵称，base 读不到对方的 wxid）
"""
from true_love_ai.memory import skill_permission_service


class PermissionDenied(Exception):
    pass


def _check_perm(users: list[str], platform: str, sender_id: str) -> bool:
    """检查 (platform, sender_id) 是否在 users 白名单内。"""
    for entry in users:
        if entry == "*":
            return True
        if ":" not in entry:
            continue
        p, u = entry.split(":", 1)
        if (p == "*" or p == platform) and (u == "*" or u == sender_id):
            return True
    return False


def check_permission(skill_name: str, ctx: dict,
                     code_permissions: list[str] | None = None) -> bool:
    """融合规则检查权限，返回 True 表示允许。

    规则2: skill 代码/DB 声明了 permissions → 用 code_permissions
    规则3: 没有代码权限，但 ctx["bot_id"] 这个机器人（或所有机器人）配了这个技能的规则 → 用它
    规则1: 都没有 → 开放
    """
    platform = ctx.get("platform", "")
    sender_id = ctx.get("sender_id", "")

    if code_permissions is not None:
        return _check_perm(code_permissions, platform, sender_id)

    configured = skill_permission_service.lookup(ctx.get("bot_id", ""), skill_name)
    if configured is not None:
        return _check_perm(configured, platform, sender_id)

    return True


def require_permission(skill_name: str, ctx: dict,
                       code_permissions: list[str] | None = None) -> None:
    """检查权限，无权限时抛 PermissionDenied。"""
    if not check_permission(skill_name, ctx, code_permissions):
        raise PermissionDenied("诶嘿~这个功能你没有权限使用哦~")
