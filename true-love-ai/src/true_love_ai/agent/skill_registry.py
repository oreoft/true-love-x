# -*- coding: utf-8 -*-
"""
Skill Registry

管理所有 AI 侧 skill 的注册和执行。
每个 skill 是一个 async callable，接收 (params: dict, ctx: dict) → str。
"""

import logging
from typing import Callable, Awaitable

LOG = logging.getLogger("SkillRegistry")

# {name: {"schema": {...}, "handler": async fn, "permissions": dict|None}}
_skills: dict[str, dict] = {}


def register_skill(schema: dict):
    """
    装饰器：注册一个 skill。

    schema 格式（OpenAI function tool schema）：
    {
        "type": "function",
        "function": {
            "name": "skill_name",
            "description": "...",
            "parameters": {...}
        },
        "permissions": ["*"]   # 可选，代码级权限（规则2）
                               # ["*"] / ["wechat:*"] / ["wechat:user1", "lark:*"]
    }

    被装饰的函数签名：async def fn(params: dict, ctx: dict) -> str
    """
    def decorator(fn: Callable[[dict, dict], Awaitable[str]]):
        name = schema["function"]["name"]
        _skills[name] = {
            "schema": schema,
            "handler": fn,
            "permissions": schema.get("permissions"),
        }
        LOG.debug("Registered skill: %s", name)
        return fn
    return decorator


def get_all_tool_schemas(platform: str = "", sender_id: str = "", bot_id: str = "") -> list[dict]:
    """获取当前用户在这个机器人上有权限使用的 skill tool schema 列表（供 LLM tools 参数使用）"""
    import copy
    from true_love_ai.agent.skills.permission import check_permission

    schemas = []
    ctx = {"platform": platform, "sender_id": sender_id, "bot_id": bot_id}
    for name, s in _skills.items():
        if not check_permission(name, ctx, s["permissions"]):
            continue
        schema = copy.deepcopy(s["schema"])
        schema.pop("notify", None)
        schema.pop("permissions", None)  # 内部字段，不传给 LLM
        params = schema.get("function", {}).get("parameters", {})
        if isinstance(params.get("properties"), dict) and not params["properties"]:
            params.pop("properties", None)
            params.pop("required", None)
        schemas.append(schema)
    return schemas


def list_skills() -> list[dict]:
    """tl-admin 配权限用：所有技能的名字、说明，以及代码里有没有写死权限（写死的在后台改不了）"""
    return [{
        "name": name,
        "description": s["schema"]["function"].get("description", ""),
        "code_permissions": s["permissions"],
    } for name, s in sorted(_skills.items())]


def get_notify(name: str) -> str | None:
    """返回 skill 的预通知消息，无则返回 None"""
    skill = _skills.get(name)
    return skill["schema"].get("notify") if skill else None


async def execute(name: str, params: dict, ctx: dict) -> str:
    """执行指定 skill（含权限检查）"""
    from true_love_ai.agent.skills.permission import require_permission

    skill = _skills.get(name)
    if not skill:
        return f"[未知 skill: {name}]"

    require_permission(name, ctx, skill["permissions"])
    return await skill["handler"](params, ctx)
