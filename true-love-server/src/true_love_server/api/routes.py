# -*- coding: utf-8 -*-
"""
Routes - 路由定义

定义所有 HTTP 接口路由。
"""

import asyncio
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Body
from true_love_common.hosts import ai_host, bot_hosts, machine_bot_id
from true_love_common.http.client import post_json
from true_love_common.media import attach_urls

from .deps import verify_token
from .exception_handlers import ApiResponse, ValidationException
from ..core import Config
from true_love_common.chat_msg import ChatMsg
from ..services import base_client
from ..services import listen_store
from ..services.listen_manager import get_listen_manager
from ..services.loki_client import get_loki_client
from ..services.group_message_repository import GroupMessageRepository

LOG = logging.getLogger("Routes")

router = APIRouter()

# 获取 ListenManager 单例
listen_manager = get_listen_manager()

AI_UNAVAILABLE_REPLY = "啊哦~AI酱 暂时连不上，稍后再试试捏~"

# 外部推送接口里代表管理员的接收者
MASTER = "master"


@router.get("/ping")
async def ping():
    """简单存活检查"""
    return "pong"


@router.get("/health")
async def health():
    """Docker 健康检查接口"""
    return {"status": "ok", "service": "true-love-server"}


@router.post("/send-msg")
async def send_msg(request: dict):
    """
    推送消息接口

    供外部调用，给管理员推送通知（部署结果等）。接收者只能是 master，
    管理员具体是谁由这台机器的 base 决定。
    """
    LOG.info("推送消息收到请求, req: %s", request)

    # 验证 token
    verify_token(request.get('token', ''))

    send_receiver = request.get('sendReceiver')
    content = request.get('content')

    # 判断是否合法发送人
    if send_receiver != MASTER or not content:
        raise ValidationException("诶嘿~接收者没注册或者内容是空的呢，检查一下吧~")

    success, error_msg = await base_client.send_to_master(content)

    if not success:
        raise ValidationException(f"呜呜~消息发送失败了捏: {error_msg}")

    return ApiResponse(data=None)


@router.post("/on-message")
async def on_message(
        request: dict,
        background_tasks: BackgroundTasks,
):
    """
    消息统一入口（Base 无脑转发所有消息）：
    - 所有消息存 DB
    - is_at_me 或私聊才触发 AI 处理
    - 通过 save() 返回值去重，防止重复触发 AI
    """
    LOG.info("聊天消息收到请求, req: %s", request)

    verify_token(request.get('token', ''))

    msg = ChatMsg.from_dict(request.get("msg", {}))
    background_tasks.add_task(_handle_incoming_message, msg)

    return ApiResponse(data="")


async def _handle_incoming_message(msg: ChatMsg) -> None:
    """存储消息（best-effort）并按需触发 AI，两个逻辑互相独立"""
    is_new = await asyncio.to_thread(_save_message, msg)
    if not is_new:
        LOG.warning("重复消息已过滤，跳过 AI 触发: msg_hash=%s sender_id=%s", msg.msg_hash, msg.sender_id)
        return

    if msg.is_at_me or not msg.is_group:
        try:
            await asyncio.to_thread(_trigger_ai, msg)
        except Exception as e:
            LOG.error(f"触发 AI 失败: {e}", exc_info=True)
            await _send_ai_unavailable(msg)


def _save_message(msg: ChatMsg) -> bool:
    """返回是否为新消息；存储失败按新消息处理，不影响触发 AI"""
    try:
        from ..core.db_engine import SessionLocal
        with SessionLocal() as db:
            return GroupMessageRepository(db).save(msg)
    except Exception as e:
        LOG.error(f"消息存储失败: {e}", exc_info=True)
        return True


def _trigger_ai(msg: ChatMsg) -> None:
    """Fire-and-forget POST 到 AI 的 /trigger 接口，AI 没接收时抛异常"""
    # AI 按 bot_id 找回复发到哪个 server；飞书消息不带 bot_id，由这台 server 接
    msg.bot_id = msg.bot_id or machine_bot_id()
    # 微信的媒体存在发消息的那台 base 上，换成 base 的 URL 让 AI 直接下载
    if msg.platform == "wechat":
        attach_urls(msg, bot_hosts(msg.bot_id).base)

    token = (Config().HTTP_TOKEN or [""])[0]
    payload = {
        "token": token,
        "msg": msg.to_dict(),
    }
    resp = post_json(
        f"{ai_host()}/trigger",
        payload,
        timeout=(5,10),
    )
    resp.raise_for_status()
    data = resp.data if isinstance(resp.data, dict) else {}
    if str(data.get("code", 0)) != "0":
        raise RuntimeError(f"AI trigger 返回业务失败: {data}")
    LOG.info("AI trigger 成功: sender_id=%s", msg.sender_id)


async def _send_ai_unavailable(msg: ChatMsg) -> None:
    """AI 没接住消息时由 server 直接回复，避免用户以为机器人假死"""
    receiver = msg.chat_id if msg.is_group else msg.sender_id
    at_user = msg.sender_id if msg.is_group else ""
    ok, err = await base_client.send_text(receiver, at_user, AI_UNAVAILABLE_REPLY, platform=msg.platform)
    if not ok:
        LOG.error("AI 不可用提示发送失败: receiver=%s err=%s", receiver, err)


# ==================== 查询接口（供 AI 回调使用）====================

@router.post("/query/history")
async def query_history(request: dict):
    """
    查询群消息历史（供 AI skill 调用）

    Body:
        - token: 鉴权 token
        - chat_id: 群聊 ID（必填）
        - sender_id:   发送者唯一 ID（可选，与 sender_name 互斥，优先级更高）
        - sender_name: 发送者昵称（可选，同名时返回所有匹配，仅在无法获取 ID 时使用）
        - platform: 平台 "wechat"(默认) | "lark"
        - limit: 最大返回条数（默认100）
        - tail_id: 游标 ID，仅返回 id < tail_id 的消息（可选，用于向前翻页）
    """
    verify_token(request.get("token", ""))

    chat_id = request.get("chat_id", "")
    platform = request.get("platform", "wechat")
    sender_id = request.get("sender_id") or None
    sender_name = request.get("sender_name") or None
    limit = int(request.get("limit", 100))
    tail_id = request.get("tail_id")
    tail_id = int(tail_id) if tail_id is not None else None

    if not chat_id:
        raise ValidationException("chat_id 不能为空")

    from ..core.db_engine import SessionLocal
    with SessionLocal() as db:
        messages = GroupMessageRepository(db).get_messages(
            chat_id,
            sender_id,
            sender_name,
            limit,
            tail_id,
            platform=platform,
        )

    LOG.info("query/history: platform=%s chat_id=%s, sender_id=%s, sender_name=%s, tail_id=%s, count=%d",
             platform, chat_id, sender_id, sender_name, tail_id, len(messages))
    return ApiResponse(data={"messages": messages})


# ==================== Listen 监听管理接口 ====================

@router.post("/listen/list")
async def listen_list(request: dict):
    """
    base 连上微信时来取要监听的群和好友

    Request Body:
        - token: 鉴权 token
    """
    verify_token(request.get("token", ""))
    return ApiResponse(data={"chats": listen_store.list_all()})


@router.get("/admin/listen/status")
async def get_listen_status():
    """
    获取监听状态

    状态定义（只有两种）：
    - healthy: 子窗口存在 AND ChatInfo 能正确响应
    - unhealthy: 子窗口不存在 OR ChatInfo 无法响应

    Returns:
        - listeners: 每个监听的状态列表
        - summary: 状态汇总 {"healthy": N, "unhealthy": M}
    """
    result = await listen_manager.get_listener_status()
    return ApiResponse(data=result)


@router.post("/admin/listen/add")
async def add_listen(request: dict):
    """
    添加监听的聊天对象

    Request Body:
        - chat_name: 聊天对象名称（好友昵称或群名）
    """
    chat_name = request.get('chat_name', '')
    if not chat_name:
        raise ValidationException("chat_name 不能为空哦~")

    result = await listen_manager.add_listen(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "添加监听失败"))

    return ApiResponse(data=result)


@router.post("/admin/listen/remove")
async def remove_listen(request: dict):
    """
    移除监听的聊天对象

    Request Body:
        - chat_name: 聊天对象名称
    """
    chat_name = request.get('chat_name', '')
    if not chat_name:
        raise ValidationException("chat_name 不能为空哦~")

    result = await listen_manager.remove_listen(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "移除监听失败"))
    return ApiResponse(data=result)


@router.post("/admin/listen/refresh")
async def refresh_listen(request: dict = Body(default={})):
    """
    智能刷新监听列表

    Returns:
        - total: 总监听数
        - success_count: 成功数
        - fail_count: 失败数
        - listeners: 每个监听的详情列表
    """
    result = await listen_manager.refresh_listen()
    # refresh 返回的是统计结果，根据 fail_count 判断是否有失败
    if result.get("fail_count", 0) > 0:
        raise ValidationException(f"刷新部分失败: {result.get('fail_count')} 个监听恢复失败")
    return ApiResponse(data=result)


@router.post("/admin/listen/reset")
async def reset_listen(request: dict):
    """
    重置单个监听

    通过关闭子窗口、移除监听、重新添加监听的方式恢复异常的监听。

    Request Body:
        - chat_name: 聊天对象名称

    Returns:
        - success: 是否成功
        - message: 结果描述
        - steps: 各步骤执行情况
    """
    chat_name = request.get('chat_name', '')
    if not chat_name:
        raise ValidationException("chat_name 不能为空哦~")

    result = await listen_manager.reset_listener(chat_name)
    if not result.get("success"):
        raise ValidationException(result.get("message", "重置监听失败"))
    return ApiResponse(data=result)


@router.post("/admin/listen/reset-all")
async def reset_all_listen(request: dict = Body(default={})):
    """
    重置所有监听

    通过停止所有监听、关闭所有子窗口、刷新 UI、重新添加所有监听的方式恢复。

    Returns:
        - success: 是否成功
        - message: 结果描述
        - total: 总监听数
        - recovered: 成功恢复的列表
        - failed: 恢复失败的列表
        - steps: 各步骤执行情况
    """
    result = await listen_manager.reset_all_listeners()
    if not result.get("success"):
        raise ValidationException(result.get("message", "重置所有监听失败"))
    return ApiResponse(data=result)


@router.post("/admin/listen/get-all-message")
async def get_all_message(request: dict):
    """
    测活接口 - 获取聊天窗口的所有消息
    
    调用 Base 的 execute/chat 接口，执行 GetAllMessage 方法。
    
    Request Body:
        - chat_name: 聊天对象名称
        
    Returns:
        - success: 是否成功
        - data: 消息列表
        - message: 结果描述
    """
    chat_name = request.get('chat_name', '')
    if not chat_name:
        raise ValidationException("chat_name 不能为空哦~")

    result = await base_client.get_wechat_client().execute_chat(chat_name, "GetAllMessage", {})
    if not result.get("success"):
        raise ValidationException(result.get("message", "获取消息失败"))
    return ApiResponse(data=result)


# ==================== Job 手动触发接口 ====================

@router.post("/action/job/run")
async def run_job(request: dict):
    """
    立即执行某个任务名下的所有定时任务（AI 手动触发用）

    Request Body:
        - job_name: 任务名称
    """
    verify_token(request.get("token", ""))
    job_name = request.get("job_name", "").strip()
    try:
        started = _ts.run_by_job_name(job_name)
    except ValueError as e:
        raise ValidationException(str(e))
    LOG.info("手动触发 job: %s, tasks=%s", job_name, started)
    return ApiResponse(data={"job_name": job_name, "status": "triggered", "tasks": len(started)})


# ==================== Loki 日志查询接口 ====================

LOKI_LOOKBACK_NS = 14 * 24 * 3600 * 1_000_000_000


@router.get("/admin/loki/logs")
async def query_loki_logs(
        before_ns: int = None,
        services: str = '',
        keyword: str = '',
        limit: int = 50
):
    """
    分页查询 Loki 日志，从新到旧

    Query Parameters:
        - before_ns: 只查这个时间点之前的日志（纳秒，不含），不传就从当前时间开始，即第一页
        - services: 逗号分隔的服务名（tl-ai,tl-base,tl-server），不传查全部
        - keyword: 关键词，不区分大小写
        - limit: 每页条数，默认 50，最多 500

    Returns:
        - logs: 日志列表（从新到旧）[{timestamp, time_str, level, service, content, raw, ts_ns}, ...]
        - next_before_ns: 下一页的 before_ns，没有更多时为空
        - has_more: 是否还有更早的日志
    """
    limit = max(1, min(limit, 500))
    end_ns = before_ns or time.time_ns()
    # 免费版只保留 14 天，往前查 14 天就覆盖了全部数据
    start_ns = end_ns - LOKI_LOOKBACK_NS
    service_list = [s.strip() for s in services.split(',') if s.strip()]

    loki_client = get_loki_client()
    result = loki_client.query_range(start_ns, end_ns, limit, service_list, keyword)

    if not result["success"]:
        raise ValidationException(result["message"])

    logs = [entry.to_dict() for entry in result["logs"]]
    has_more = len(logs) >= limit
    next_before_ns = str(min(int(log["ts_ns"]) for log in logs)) if logs and has_more else ""

    return ApiResponse(data={
        "logs": logs,
        "next_before_ns": next_before_ns,
        "has_more": has_more,
        "count": len(logs)
    })


# ==================== Admin 定时提醒管理接口 ====================

from ..services import reminder_service as _rs


@router.get("/admin/reminder/list")
async def list_reminders():
    result = _rs.list_all_reminders()
    LOG.info("admin/reminder/list: count=%d", len(result))
    return ApiResponse(data={"jobs": result, "total": len(result)})


@router.post("/admin/reminder/add")
async def admin_add_reminder(request: dict):
    receiver = request.get("receiver", "").strip()
    content = request.get("content", "").strip()
    target_time_iso = request.get("target_time_iso", "").strip()
    at_user = request.get("at_user", "").strip()
    platform = request.get("platform", "wechat").strip()
    if not receiver or not content or not target_time_iso:
        raise ValidationException("receiver、content、target_time_iso 不能为空")
    job_id = _rs.make_job_id(receiver)
    try:
        data = _rs.add_reminder(job_id, target_time_iso, receiver, content, at_user, platform)
    except ValueError as e:
        raise ValidationException(str(e))
    LOG.info("admin/reminder/add: job_id=%s", job_id)
    return ApiResponse(data=data)


@router.post("/admin/reminder/update")
async def admin_update_reminder(request: dict):
    job_id = request.get("job_id", "").strip()
    receiver = request.get("receiver", "").strip()
    content = request.get("content", "").strip()
    target_time_iso = request.get("target_time_iso", "").strip()
    at_user = request.get("at_user", "").strip()
    platform = request.get("platform", "wechat").strip()
    if not job_id or not receiver or not content or not target_time_iso:
        raise ValidationException("job_id、receiver、content、target_time_iso 不能为空")
    try:
        data = _rs.edit_reminder(job_id, receiver, content, target_time_iso, at_user, platform)
    except ValueError as e:
        raise ValidationException(str(e))
    LOG.info("admin/reminder/update: job_id=%s", job_id)
    return ApiResponse(data=data)


@router.post("/admin/reminder/delete")
async def admin_delete_reminder(request: dict):
    job_id = request.get("job_id", "").strip()
    if not job_id:
        raise ValidationException("job_id 不能为空")
    try:
        data = _rs.delete_reminder(job_id)
    except ValueError as e:
        raise ValidationException(str(e))
    LOG.info("admin/reminder/delete: job_id=%s", job_id)
    return ApiResponse(data=data)


# ==================== Admin 定时任务管理接口 ====================

from ..services import task_service as _ts


def _task_call(func, *args):
    try:
        return func(*args)
    except ValueError as e:
        raise ValidationException(str(e))


@router.get("/admin/task/list")
async def list_tasks():
    tasks = _ts.list_tasks()
    LOG.info("admin/task/list: count=%d", len(tasks))
    return ApiResponse(data={
        "tasks": tasks,
        "jobs": _ts.job_names(),
        "timezones": [{"value": key, "label": label} for key, label in _ts.TIMEZONES.items()],
    })


@router.post("/admin/task/add")
async def admin_add_task(request: dict):
    data = _task_call(_ts.add_task, request.get("job_name", ""), request.get("receivers"), request.get("schedule"))
    return ApiResponse(data=data)


@router.post("/admin/task/update")
async def admin_update_task(request: dict):
    data = _task_call(_ts.update_task, request.get("task_id", ""), request.get("job_name", ""),
                      request.get("receivers"), request.get("schedule"))
    return ApiResponse(data=data)


@router.post("/admin/task/delete")
async def admin_delete_task(request: dict):
    return ApiResponse(data=_task_call(_ts.delete_task, request.get("task_id", "")))


@router.post("/admin/task/run")
async def admin_run_task(request: dict):
    return ApiResponse(data=_task_call(_ts.run_now, request.get("task_id", "")))


# ==================== Admin 动态技能管理接口 ====================

from ..services import ai_skill_client as _skill_client


@router.get("/admin/skill/list")
async def admin_list_skills():
    try:
        skills = await _skill_client.list_skills()
    except RuntimeError as e:
        raise ValidationException(str(e))
    LOG.info("admin/skill/list: count=%d", len(skills))
    return ApiResponse(data={"skills": skills, "total": len(skills)})


@router.post("/admin/skill/save")
async def admin_save_skill(request: dict):
    skill_id = request.get("id", "").strip()
    name = request.get("name", "").strip()
    description = request.get("description", "").strip()
    command = request.get("command", "").strip()
    parameters = request.get("parameters") or ""
    permissions = request.get("permissions") or None
    if not skill_id or not name or not description or not command:
        raise ValidationException("id、name、description、command 不能为空")
    try:
        data = await _skill_client.save_skill(
            skill_id, name, description, command,
            parameters.strip() if parameters.strip() else None,
            permissions,
        )
    except RuntimeError as e:
        raise ValidationException(str(e))
    LOG.info("admin/skill/save: id=%s", skill_id)
    return ApiResponse(data=data)


@router.post("/admin/skill/delete")
async def admin_delete_skill(request: dict):
    skill_id = request.get("id", "").strip()
    if not skill_id:
        raise ValidationException("id 不能为空")
    try:
        data = await _skill_client.delete_skill(skill_id)
    except RuntimeError as e:
        raise ValidationException(str(e))
    LOG.info("admin/skill/delete: id=%s", skill_id)
    return ApiResponse(data=data)
