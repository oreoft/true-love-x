# -*- coding: utf-8 -*-
"""
AI 的管理接口，tl-admin 的请求经 server 转发过去

/admin/skill/list、/admin/skill/save、/admin/skill/delete 均为 POST，token 放在请求体中。
"""
import logging

from true_love_common.hosts import ai_host
from true_love_common.http.client import async_post_json

from ._common import token as _token

LOG = logging.getLogger("AiAdminClient")


async def list_skills() -> list[dict]:
    result = await async_post_json(
        f"{ai_host()}/admin/skill/list",
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
        f"{ai_host()}/admin/skill/save",
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
        f"{ai_host()}/admin/skill/delete",
        {"token": _token(), "id": skill_id},
        timeout=10.0,
    )
    if not result.ok:
        raise RuntimeError(f"删除技能失败: {result.error or result.text}")
    data = result.data or {}
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "删除技能失败"))
    return data.get("data", {})
