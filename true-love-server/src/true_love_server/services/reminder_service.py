# -*- coding: utf-8 -*-
"""
Reminder Service - 定时提醒业务逻辑

提醒存在所属机器人的库里（调度器里别名是 bot_id 的 jobstore），到点从这个机器人发出去。
AI 回调接口（有 token）和 tl-admin（无 token）共用此模块。
"""
import asyncio
import logging
import time

from .scheduler_service import get_job, get_jobs, scheduler

LOG = logging.getLogger("ReminderService")

ID_PREFIX = "reminder_"


def _send_reminder(receiver: str, at_user: str, content: str, job_id: str, bot_id: str) -> None:
    """APScheduler 触发函数（模块级，SQLAlchemy jobstore 要求可序列化）"""
    LOG.info("提醒触发: job_id=%s, bot_id=%s, receiver=%s", job_id, bot_id, receiver)
    try:
        from ..services import base_client
        success, msg = asyncio.run(base_client.send_text(
            bot_id,
            receiver,
            at_user,
            f"⏰ 【提醒功能】：\n\n{content}",
        ))
        if not success:
            LOG.error("提醒发送失败: %s", msg)
    except Exception as exc:
        LOG.exception("提醒触发异常: %s", exc)


def _parse_future_dt(iso_str: str):
    """解析 ISO-8601 时间字符串，校验必须是未来时间，返回 datetime 对象。"""
    import dateutil.parser
    from datetime import datetime
    dt = dateutil.parser.isoparse(iso_str)
    now = datetime.now(dt.tzinfo)
    if dt <= now:
        raise ValueError(f"目标时间 {iso_str} 已是过去时间")
    return dt


def _get(bot_id: str, job_id: str):
    job = get_job(bot_id, job_id) if job_id.startswith(ID_PREFIX) else None
    if not job:
        raise ValueError(f"未找到提醒任务: {job_id}")
    return job


def add_reminder(bot_id: str, job_id: str, target_time_iso: str, receiver: str,
                 content: str, at_user: str = "") -> dict:
    """新增或覆盖提醒任务，返回 {"job_id": ...}。"""
    dt = _parse_future_dt(target_time_iso)
    scheduler.add_job(
        _send_reminder,
        'date',
        run_date=dt,
        id=job_id,
        jobstore=bot_id,
        replace_existing=True,
        kwargs={
            "receiver": receiver,
            "at_user": at_user,
            "content": content,
            "job_id": job_id,
            "bot_id": bot_id,
        },
    )
    LOG.info("reminder/add: bot_id=%s job_id=%s time=%s receiver=%s",
             bot_id, job_id, target_time_iso, receiver)
    return {"job_id": job_id}


def delete_reminder(bot_id: str, job_id: str) -> dict:
    """删除提醒任务，job 不存在时抛 ValueError。"""
    _get(bot_id, job_id)
    scheduler.remove_job(job_id, jobstore=bot_id)
    LOG.info("reminder/delete: bot_id=%s job_id=%s", bot_id, job_id)
    return {"job_id": job_id}


def update_reminder(bot_id: str, job_id: str, new_time_iso: str = "", new_content: str = "") -> dict:
    """修改提醒的时间或内容（至少一个），返回 {"job_id": ..., "next_run_time": ...}。"""
    if not new_time_iso and not new_content:
        raise ValueError("new_time_iso 和 new_content 至少提供一个")

    job = _get(bot_id, job_id)
    old_kwargs = dict(job.kwargs or {})
    dt = _parse_future_dt(new_time_iso) if new_time_iso else job.next_run_time
    new_kwargs = {**old_kwargs, "content": new_content or old_kwargs.get("content", ""), "bot_id": bot_id}

    scheduler.add_job(
        _send_reminder,
        'date',
        run_date=dt,
        id=job_id,
        jobstore=bot_id,
        replace_existing=True,
        kwargs=new_kwargs,
    )
    LOG.info("reminder/update: bot_id=%s job_id=%s new_time=%s new_content=%s",
             bot_id, job_id, dt.isoformat(), new_kwargs.get("content", ""))
    return {"job_id": job_id, "next_run_time": dt.isoformat()}


def edit_reminder(bot_id: str, job_id: str, receiver: str, content: str, target_time_iso: str,
                  at_user: str = "") -> dict:
    """后台修改提醒，所有字段一起改；接收者变了就换一个 job_id（AI 按接收者前缀查提醒）"""
    job = _get(bot_id, job_id)
    old_receiver = (job.kwargs or {}).get("receiver", "")
    new_id = job_id if receiver == old_receiver else make_job_id(receiver)
    data = add_reminder(bot_id, new_id, target_time_iso, receiver, content, at_user)
    if new_id != job_id:
        scheduler.remove_job(job_id, jobstore=bot_id)
    LOG.info("reminder/edit: bot_id=%s job_id=%s -> %s", bot_id, job_id, new_id)
    return data


def query_reminders(bot_id: str, receiver: str = "") -> list[dict]:
    """按接收者查这个机器人的提醒（AI 用），返回精简字段列表。"""
    result = []
    for job in get_jobs(bot_id):
        if not job.id.startswith(f"{ID_PREFIX}{receiver}_" if receiver else ID_PREFIX):
            continue
        if job.next_run_time:
            kwargs = job.kwargs or {}
            result.append({
                "job_id": job.id,
                "content": kwargs.get("content", ""),
                "next_run_time": job.next_run_time.isoformat(),
            })
    return result


def list_all_reminders(bot_id: str) -> list[dict]:
    """这个机器人所有待执行的提醒（后台用），含完整字段，按时间升序。"""
    result = []
    for job in get_jobs(bot_id):
        if not job.id.startswith(ID_PREFIX) or not job.next_run_time:
            continue
        kwargs = job.kwargs or {}
        result.append({
            "job_id": job.id,
            "receiver": kwargs.get("receiver", ""),
            "content": kwargs.get("content", ""),
            "at_user": kwargs.get("at_user", ""),
            "next_run_time": job.next_run_time.isoformat(),
        })
    result.sort(key=lambda x: x["next_run_time"])
    return result


def make_job_id(receiver: str) -> str:
    """生成提醒任务 ID。"""
    return f"{ID_PREFIX}{receiver}_{int(time.time())}"
