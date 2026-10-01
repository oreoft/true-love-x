# -*- coding: utf-8 -*-
"""监听管理 Skill（通过 Server /action/listen/* 实现，谁能用在 tl-admin 的技能页设置）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("ListenSkill")



@register_skill({
    "type": "function",
    "function": {
        "name": "listen_manage",
        "description": (
            "管理微信监听列表，支持新增、删除监听对象。"
            "仅限管理员私聊使用。"
            "当管理员说'新增监听xxx'、'删除监听xxx'时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "remove"],
                    "description": "操作类型：add=新增, remove=删除"
                },
                "target": {
                    "type": "string",
                    "description": "监听对象名称（群名或好友昵称）"
                }
            },
            "required": ["action", "target"]
        }
    }
})
async def listen_manage(params: dict, ctx: dict) -> str:
    action = params.get("action", "")
    target = params.get("target", "")
    if not target:
        return "诶嘿~请告诉我要操作的监听对象名称哦~"

    from true_love_ai.agent.server_client import listen_add, listen_remove
    if action == "add":
        result = await listen_add(target)
        if result.get("code") == 0:
            return f"好耶~添加监听成功: {target}"
        raise SkillFailed(f"呜呜~添加监听失败: {result.get('message', '未知错误')}")

    if action == "remove":
        result = await listen_remove(target)
        if result.get("code") == 0:
            return f"好耶~删除监听成功: {target}"
        raise SkillFailed(f"呜呜~删除监听失败: {result.get('message', '未知错误')}")

    return "诶嘿~不支持该操作，支持：add / remove"
