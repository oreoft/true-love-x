# -*- coding: utf-8 -*-
"""
Admin AI Routes - tl-admin 里存在 AI 那边的设置，原样转发给 AI 的管理接口

- /admin/skill/*：动态技能，所有机器人共用
- /admin/bots/{bot_id}/personas*：人设（system prompt 和语音风格）
- /admin/bots/{bot_id}/permissions*：技能权限
- /admin/models*：模型，所有机器人共用
人设和技能权限按机器人存；请求里带 all_bots=true 时改的是所有机器人共用的那份（AI 那边的 bot_id 是 "*"）。
"""

import logging

from fastapi import APIRouter

from . import deps
from .exception_handlers import ApiResponse, ValidationException
from ..services.ai_client import admin as ai_admin

LOG = logging.getLogger("AdminAiRoutes")

admin_ai_router = APIRouter(prefix="/admin")

ALL_BOTS = "*"


async def _forward(call, *args):
    """调 AI，把 AI 的错误文案原样给前端"""
    try:
        return await call(*args)
    except RuntimeError as e:
        raise ValidationException(str(e))


def _scope(bot_id: str, request: dict) -> str:
    """这次改的是哪个机器人：登记过的机器人，或者所有机器人"""
    return ALL_BOTS if request.get("all_bots") else deps.bot(bot_id).bot_id


# ==================== 动态技能 ====================

@admin_ai_router.get("/skill/list")
async def list_skills():
    skills = await _forward(ai_admin.list_skills)
    return ApiResponse(data={"skills": skills, "total": len(skills)})


@admin_ai_router.post("/skill/save")
async def save_skill(request: dict):
    skill_id = request.get("id", "").strip()
    name = request.get("name", "").strip()
    description = request.get("description", "").strip()
    command = request.get("command", "").strip()
    parameters = request.get("parameters") or ""
    permissions = request.get("permissions") or None
    if not skill_id or not name or not description or not command:
        raise ValidationException("id、name、description、command 不能为空")
    data = await _forward(ai_admin.save_skill, skill_id, name, description, command,
                          parameters.strip() if parameters.strip() else None, permissions)
    LOG.info("admin/skill/save: id=%s", skill_id)
    return ApiResponse(data=data)


@admin_ai_router.post("/skill/delete")
async def delete_skill(request: dict):
    skill_id = request.get("id", "").strip()
    if not skill_id:
        raise ValidationException("id 不能为空")
    data = await _forward(ai_admin.delete_skill, skill_id)
    LOG.info("admin/skill/delete: id=%s", skill_id)
    return ApiResponse(data=data)


# ==================== 人设 ====================

@admin_ai_router.get("/bots/{bot_id}/personas")
async def list_personas(bot_id: str):
    """这个机器人的人设和所有机器人共用的默认"""
    return ApiResponse(data=await _forward(ai_admin.list_personas, deps.bot(bot_id).bot_id))


@admin_ai_router.post("/bots/{bot_id}/personas/save")
async def save_persona(bot_id: str, request: dict):
    scope = _scope(bot_id, request)
    data = await _forward(ai_admin.save_persona, scope, request.get("chat", ""),
                          request.get("prompt", ""), request.get("voice_style", ""))
    LOG.info("admin/personas/save: bot=%s chat=%s", scope, request.get("chat", ""))
    return ApiResponse(data=data)


@admin_ai_router.post("/bots/{bot_id}/personas/delete")
async def delete_persona(bot_id: str, request: dict):
    scope = _scope(bot_id, request)
    data = await _forward(ai_admin.delete_persona, scope, request.get("chat", ""))
    LOG.info("admin/personas/delete: bot=%s chat=%s", scope, request.get("chat", ""))
    return ApiResponse(data=data)


# ==================== 技能权限 ====================

@admin_ai_router.get("/bots/{bot_id}/permissions")
async def list_permissions(bot_id: str):
    """这个机器人自己的规则、所有机器人共用的规则，和可以配权限的技能"""
    return ApiResponse(data=await _forward(ai_admin.list_permissions, deps.bot(bot_id).bot_id))


@admin_ai_router.post("/bots/{bot_id}/permissions/save")
async def save_permission(bot_id: str, request: dict):
    scope = _scope(bot_id, request)
    data = await _forward(ai_admin.save_permission, scope, request.get("skill", ""), request.get("users"))
    LOG.info("admin/permissions/save: bot=%s skill=%s", scope, request.get("skill", ""))
    return ApiResponse(data=data)


@admin_ai_router.post("/bots/{bot_id}/permissions/delete")
async def delete_permission(bot_id: str, request: dict):
    scope = _scope(bot_id, request)
    data = await _forward(ai_admin.delete_permission, scope, request.get("skill", ""))
    LOG.info("admin/permissions/delete: bot=%s skill=%s", scope, request.get("skill", ""))
    return ApiResponse(data=data)


# ==================== 模型 ====================

@admin_ai_router.get("/models")
async def list_models():
    return ApiResponse(data={"models": await _forward(ai_admin.list_models)})


@admin_ai_router.post("/models/save")
async def save_model(request: dict):
    """改一个类别的主力或备用模型；value 为空时恢复默认"""
    models = await _forward(ai_admin.save_model, request.get("category", ""), request.get("key", ""),
                            request.get("value") or "")
    LOG.info("admin/models/save: %s.%s=%s", request.get("category"), request.get("key"), request.get("value"))
    return ApiResponse(data={"models": models})
