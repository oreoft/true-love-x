# -*- coding: utf-8 -*-
"""
Admin Routes - AI 的管理接口（/admin/*）

tl-admin 的请求经 server 转发过来：技能和技能权限、人设、模型。
所有端点使用 POST，token 放在请求体中。给 server 的业务接口在 trigger_routes、data_routes。
"""
import logging

from fastapi import APIRouter

from true_love_ai.api.deps import verify_token
from true_love_ai.core.model_registry import get_model_registry
from true_love_ai.memory import dynamic_skill_service as _ss
from true_love_ai.memory import persona_service, skill_access_service
from true_love_ai.models.response import APIResponse

LOG = logging.getLogger("AdminRoutes")

admin_router = APIRouter(prefix="/admin")


@admin_router.post("/skill/list")
async def list_skills(request: dict):
    """内置技能和安装的技能，各自带权限点；外加新内置技能的默认权限点"""
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    from true_love_ai.agent import skill_registry
    from true_love_ai.agent.skills import ensure_skills_loaded
    ensure_skills_loaded()
    access = skill_access_service.list_all()
    builtin = [{**s, "permissions": access.get(s["name"], {}).get("points", ["*:*"])}
               for s in skill_registry.list_skills()]
    installed = _ss.list_skills()
    LOG.info("admin/skill/list: builtin=%d installed=%d", len(builtin), len(installed))
    return APIResponse.success({
        "builtin": builtin,
        "installed": installed,
        "default_permissions": [skill_access_service.format_point(p) for p in skill_access_service.default_points()],
    })


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
        result = _ss.save_skill(skill_id, name, description, command, parameters, creator, points=permissions)
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


@admin_router.post("/skill/permissions/save")
async def save_skill_permissions(request: dict):
    """改一个技能（内置的或安装的）的权限点"""
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    skill = request.get("skill", "").strip()
    try:
        points = skill_access_service.set_points(skill, request.get("permissions"))
    except ValueError as e:
        return APIResponse.error(str(e))
    return APIResponse.success({"skill": skill,
                                "permissions": [skill_access_service.format_point(p) for p in points]})


@admin_router.post("/skill/default-permissions/save")
async def save_default_permissions(request: dict):
    """改新内置技能第一次进库时给的权限点"""
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    try:
        points = skill_access_service.set_default_points(request.get("permissions"))
    except ValueError as e:
        return APIResponse.error(str(e))
    return APIResponse.success({"permissions": [skill_access_service.format_point(p) for p in points]})


# ==================== 画像 ====================

@admin_router.post("/memory/get")
async def get_memory(request: dict):
    """一个会话（这个机器人里的群或私聊）里某个人的画像；只读"""
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    bot_id, chat = request.get("bot_id", "").strip(), request.get("chat", "").strip()
    sender = request.get("sender", "").strip()
    if not bot_id or not chat or not sender:
        return APIResponse.error("bot_id、chat 和 sender 不能为空")
    from true_love_ai.memory.memory_manager import describe_user_memory
    return APIResponse.success({"facts": describe_user_memory(f"{bot_id}:{chat}", sender)})


# ==================== 人设 ====================

@admin_router.post("/persona/list")
async def list_personas(request: dict):
    """这个机器人的人设，加上所有机器人共用的默认（bot_id="*"）"""
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    bot_id = request.get("bot_id", "").strip()
    if not bot_id:
        return APIResponse.error("bot_id 不能为空")
    return APIResponse.success({"personas": persona_service.list_personas(bot_id),
                                "fallback_prompt": persona_service.FALLBACK_PROMPT})


@admin_router.post("/persona/save")
async def save_persona(request: dict):
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    try:
        data = persona_service.save_persona(request.get("bot_id", ""), request.get("chat", ""),
                                            request.get("prompt", ""), request.get("voice_style", ""))
    except ValueError as e:
        return APIResponse.error(str(e))
    return APIResponse.success(data)


@admin_router.post("/persona/delete")
async def delete_persona(request: dict):
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    if not persona_service.delete_persona(request.get("bot_id", ""), request.get("chat", "")):
        return APIResponse.error("没有这条人设")
    return APIResponse.success({})


# ==================== 模型（所有机器人共用） ====================

@admin_router.post("/model/list")
async def list_models(request: dict):
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    return APIResponse.success({"models": get_model_registry().describe()})


@admin_router.post("/model/save")
async def save_model(request: dict):
    """改一个类别的主力或备用模型；value 为空时恢复默认"""
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()
    try:
        get_model_registry().set(request.get("category", ""), request.get("key", ""), request.get("value") or "")
    except ValueError as e:
        return APIResponse.error(str(e))
    return APIResponse.success({"models": get_model_registry().describe()})
