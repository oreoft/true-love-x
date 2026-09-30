# -*- coding: utf-8 -*-
"""
Admin Platform Routes - tl-admin 里和机器人无关的管理接口（/admin/*）

- /admin/tasks/options：定时任务可选的任务方法和时区
- /admin/loki/logs：查 Loki 日志，可以按服务和机器人筛选
- /admin/skill/*：动态技能，转发给 AI 的管理接口
按机器人管理的接口在 admin_bot_routes。
"""

import logging
import time

from fastapi import APIRouter

from .exception_handlers import ApiResponse, ValidationException
from ..services import task_service
from ..services.ai_client import admin as ai_admin
from ..services.loki_client import get_loki_client

LOG = logging.getLogger("AdminPlatformRoutes")

admin_platform_router = APIRouter(prefix="/admin")

# Loki 免费版只保留 14 天，往前查 14 天就覆盖了全部数据
LOKI_LOOKBACK_NS = 14 * 24 * 3600 * 1_000_000_000


@admin_platform_router.get("/tasks/options")
async def task_options():
    """新增定时任务时的下拉选项"""
    return ApiResponse(data={
        "jobs": task_service.job_names(),
        "timezones": [{"value": key, "label": label} for key, label in task_service.TIMEZONES.items()],
    })


# ==================== 日志 ====================

@admin_platform_router.get("/loki/logs")
async def query_loki_logs(before_ns: int = None, services: str = '', keyword: str = '', bot_id: str = '',
                          limit: int = 50):
    """
    分页查询 Loki 日志，从新到旧

    Query Parameters:
        - before_ns: 只查这个时间点之前的日志（纳秒，不含），不传就从当前时间开始，即第一页
        - services: 逗号分隔的服务名（tl-ai,tl-base,tl-server），不传查全部
        - keyword: 关键词，不区分大小写
        - bot_id: 只看这个机器人的 base 日志（server 和 AI 的日志不带机器人标签）
        - limit: 每页条数，默认 50，最多 500

    Returns:
        - logs: 日志列表（从新到旧）[{timestamp, time_str, level, service, content, raw, ts_ns}, ...]
        - next_before_ns: 下一页的 before_ns，没有更多时为空
        - has_more: 是否还有更早的日志
    """
    limit = max(1, min(limit, 500))
    end_ns = before_ns or time.time_ns()
    start_ns = end_ns - LOKI_LOOKBACK_NS
    service_list = [s.strip() for s in services.split(',') if s.strip()]

    result = get_loki_client().query_range(start_ns, end_ns, limit, service_list, keyword, bot_id)
    if not result["success"]:
        raise ValidationException(result["message"])

    logs = [entry.to_dict() for entry in result["logs"]]
    has_more = len(logs) >= limit
    next_before_ns = str(min(int(log["ts_ns"]) for log in logs)) if logs and has_more else ""
    return ApiResponse(data={"logs": logs, "next_before_ns": next_before_ns, "has_more": has_more,
                             "count": len(logs)})


# ==================== 动态技能（转发给 AI） ====================

@admin_platform_router.get("/skill/list")
async def list_skills():
    try:
        skills = await ai_admin.list_skills()
    except RuntimeError as e:
        raise ValidationException(str(e))
    return ApiResponse(data={"skills": skills, "total": len(skills)})


@admin_platform_router.post("/skill/save")
async def save_skill(request: dict):
    skill_id = request.get("id", "").strip()
    name = request.get("name", "").strip()
    description = request.get("description", "").strip()
    command = request.get("command", "").strip()
    parameters = request.get("parameters") or ""
    permissions = request.get("permissions") or None
    if not skill_id or not name or not description or not command:
        raise ValidationException("id、name、description、command 不能为空")
    try:
        data = await ai_admin.save_skill(skill_id, name, description, command,
                                         parameters.strip() if parameters.strip() else None, permissions)
    except RuntimeError as e:
        raise ValidationException(str(e))
    LOG.info("admin/skill/save: id=%s", skill_id)
    return ApiResponse(data=data)


@admin_platform_router.post("/skill/delete")
async def delete_skill(request: dict):
    skill_id = request.get("id", "").strip()
    if not skill_id:
        raise ValidationException("id 不能为空")
    try:
        data = await ai_admin.delete_skill(skill_id)
    except RuntimeError as e:
        raise ValidationException(str(e))
    LOG.info("admin/skill/delete: id=%s", skill_id)
    return ApiResponse(data=data)
