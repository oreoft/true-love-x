# -*- coding: utf-8 -*-
"""
Admin Routes - AI 的管理接口（/admin/*）

tl-admin 的请求经 server 转发过来，目前只有动态技能管理。
所有端点使用 POST，token 放在请求体中。给 server 的业务接口在 trigger_routes、data_routes。
"""
import logging

from fastapi import APIRouter

from true_love_ai.api.deps import verify_token
from true_love_ai.memory import dynamic_skill_service as _ss
from true_love_ai.models.response import APIResponse

LOG = logging.getLogger("AdminRoutes")

admin_router = APIRouter(prefix="/admin")


@admin_router.post("/skill/list")
async def list_skills(request: dict):
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    data = _ss.list_skills()
    LOG.info("admin/skill/list: count=%d", len(data))
    return APIResponse.success({"skills": data, "total": len(data)})


@admin_router.post("/skill/save")
async def save_skill(request: dict):
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()

    skill_id = request.get("id", "").strip()
    name = request.get("name", "").strip()
    description = request.get("description", "").strip()
    command = request.get("command", "").strip()
    parameters = request.get("parameters") or ""
    permissions = request.get("permissions") or None
    creator = request.get("creator", "admin").strip()

    try:
        result = _ss.save_skill(skill_id, name, description, command,
                                parameters, creator, permissions)
    except (ValueError, RuntimeError) as e:
        return APIResponse.error(str(e))

    LOG.info("admin/skill/save: id=%s", result["id"])
    return APIResponse.success({"id": result["id"]})


@admin_router.post("/skill/delete")
async def delete_skill(request: dict):
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    skill_id = request.get("id", "").strip()
    try:
        result = _ss.delete_skill(skill_id)
    except ValueError as e:
        return APIResponse.error(str(e))
    LOG.info("admin/skill/delete: id=%s", skill_id)
    return APIResponse.success(result)
