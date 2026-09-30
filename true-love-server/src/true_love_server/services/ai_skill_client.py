# -*- coding: utf-8 -*-
"""
AI Skill Client - 调用 AI 服务的动态技能管理 API

/skill/list、/skill/save、/skill/delete 均为 POST，token 放在请求体中。
"""
import logging

from true_love_common.hosts import ai_host
from true_love_common.http.client import async_post_json

from ..core import Config

LOG = logging.getLogger("AiSkillClient")


def _token() -> str:
    tokens = Config().HTTP_TOKEN or []
    return tokens[0] if tokens else ""


async def list_skills() -> list[dict]:
    result = await async_post_json(
        f"{ai_host()}/skill/list",
        {"token": _token()},
        timeout=10.0,
    )
    if not result.ok:
        raise RuntimeError(f"获取技能列表失败: {result.error or result.text}")
    data = result.data or {}
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取技能列表失败"))
    return data.get("data", {}).get("skills", [])


async def save_skill(skill_id: str, name: str, description: str,
                     command: str, parameters: str | None,
                     permissions=None) -> dict:
    result = await async_post_json(
        f"{ai_host()}/skill/save",
        {
            "token": _token(),
            "id": skill_id,
            "name": name,
            "description": description,
            "command": command,
            "parameters": parameters,
            "permissions": permissions,
            "creator": "admin",
        },
        timeout=10.0,
    )
    if not result.ok:
        raise RuntimeError(f"保存技能失败: {result.error or result.text}")
    data = result.data or {}
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "保存技能失败"))
    return data.get("data", {})


async def delete_skill(skill_id: str) -> dict:
    result = await async_post_json(
        f"{ai_host()}/skill/delete",
        {"token": _token(), "id": skill_id},
        timeout=10.0,
    )
    if not result.ok:
        raise RuntimeError(f"删除技能失败: {result.error or result.text}")
    data = result.data or {}
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "删除技能失败"))
    return data.get("data", {})
