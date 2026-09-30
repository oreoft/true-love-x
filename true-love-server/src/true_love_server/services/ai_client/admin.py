# -*- coding: utf-8 -*-
"""
AI 的管理接口，tl-admin 的请求经 server 转发过去

都是 POST，token 放在请求体中：
- /admin/skill/list、/admin/skill/save、/admin/skill/delete：动态技能
- /admin/persona/list、/admin/persona/save、/admin/persona/delete：人设（按机器人，"*" 是所有机器人共用）
- /admin/permission/list、/admin/permission/save、/admin/permission/delete：技能权限（同上）
- /admin/model/list、/admin/model/save：模型（所有机器人共用）
"""
import logging

from true_love_common.hosts import ai_host
from true_love_common.http.client import async_post_json

from ._common import token as _token

LOG = logging.getLogger("AiAdminClient")


async def _call(path: str, payload: dict, action: str) -> dict:
    """调 AI 的管理接口，返回 data；失败时抛带 AI 文案的 RuntimeError"""
    result = await async_post_json(f"{ai_host()}{path}", {"token": _token(), **payload}, timeout=10.0)
    if not result.ok:
        raise RuntimeError(f"{action}失败: {result.error or result.text}")
    data = result.data or {}
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", f"{action}失败"))
    return data.get("data") or {}


# ==================== 动态技能 ====================

async def list_skills() -> list[dict]:
    return (await _call("/admin/skill/list", {}, "获取技能列表")).get("skills", [])


async def save_skill(skill_id: str, name: str, description: str,
                     command: str, parameters: str | None,
                     permissions=None) -> dict:
    return await _call("/admin/skill/save", {
        "id": skill_id,
        "name": name,
        "description": description,
        "command": command,
        "parameters": parameters,
        "permissions": permissions,
        "creator": "admin",
    }, "保存技能")


async def delete_skill(skill_id: str) -> dict:
    return await _call("/admin/skill/delete", {"id": skill_id}, "删除技能")


# ==================== 人设 ====================

async def list_personas(bot_id: str) -> dict:
    return await _call("/admin/persona/list", {"bot_id": bot_id}, "获取人设")


async def save_persona(bot_id: str, chat: str, prompt: str, voice_style: str) -> dict:
    return await _call("/admin/persona/save",
                       {"bot_id": bot_id, "chat": chat, "prompt": prompt, "voice_style": voice_style}, "保存人设")


async def delete_persona(bot_id: str, chat: str) -> dict:
    return await _call("/admin/persona/delete", {"bot_id": bot_id, "chat": chat}, "删除人设")


# ==================== 技能权限 ====================

async def list_permissions(bot_id: str) -> dict:
    return await _call("/admin/permission/list", {"bot_id": bot_id}, "获取技能权限")


async def save_permission(bot_id: str, skill: str, users: list[str]) -> dict:
    return await _call("/admin/permission/save", {"bot_id": bot_id, "skill": skill, "users": users}, "保存技能权限")


async def delete_permission(bot_id: str, skill: str) -> dict:
    return await _call("/admin/permission/delete", {"bot_id": bot_id, "skill": skill}, "删除技能权限")


# ==================== 模型 ====================

async def list_models() -> list[dict]:
    return (await _call("/admin/model/list", {}, "获取模型")).get("models", [])


async def save_model(category: str, key: str, value: str) -> list[dict]:
    return (await _call("/admin/model/save", {"category": category, "key": key, "value": value},
                        "保存模型")).get("models", [])
