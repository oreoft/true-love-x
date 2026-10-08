# -*- coding: utf-8 -*-
"""
Skill Registry

管理所有 AI 侧 skill 的注册和执行。
每个 skill 是一个 async callable，接收 (params: dict, ctx: dict) → str。
"""

import logging
from typing import Callable, Awaitable

LOG = logging.getLogger("SkillRegistry")


class SkillFailed(Exception):
    """
    技能没办成。str(e) 是回给模型的话（沿用技能自己的语气），模型据此回复用户。

    AgentLoop 会记日志（raise ... from e 时带上原异常的堆栈），这条消息的结局记成 tool_failed。
    技能里别再自己 catch-all 后返回失败文案，要么直接抛原异常，要么包成这个。
    """


# {name: {"schema": {...}, "handler": async fn}}；谁能用存在库里（skill_access_service），代码里不写
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
        "notify": [...]   # 可选，执行前先发给用户的提示
    }

    被装饰的函数签名：async def fn(params: dict, ctx: dict) -> str
    """
    def decorator(fn: Callable[[dict, dict], Awaitable[str]]):
        name = schema["function"]["name"]
        _skills[name] = {
            "schema": schema,
            "handler": fn,
        }
        LOG.debug("Registered skill: %s", name)
        return fn
    return decorator


def get_all_tool_schemas(ctx: dict) -> list[dict]:
    """当前这个人在这里能用的 skill tool schema 列表（供 LLM tools 参数使用）；ctx 的字段见 permission"""
    import copy
    from true_love_ai.agent.skills.permission import check_permission

    schemas = []
    for name, s in _skills.items():
        if not check_permission(name, ctx):
            continue
        schema = copy.deepcopy(s["schema"])
        schema.pop("notify", None)
        schema.pop("timeout", None)
        params = schema.get("function", {}).get("parameters", {})
        if isinstance(params.get("properties"), dict) and not params["properties"]:
            params.pop("properties", None)
            params.pop("required", None)
        schemas.append(schema)
    return schemas


def names() -> list[str]:
    return sorted(_skills)


def list_skills() -> list[dict]:
    """tl-admin 用：所有内置技能的名字和说明"""
    return [{"name": name, "description": s["schema"]["function"].get("description", "")}
            for name, s in sorted(_skills.items())]


def get_timeout(name: str, default: int) -> int:
    """skill 自己声明的执行超时（秒），没声明用 default"""
    skill = _skills.get(name)
    return skill["schema"].get("timeout", default) if skill else default


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

    require_permission(name, ctx)
    return await skill["handler"](params, ctx)
