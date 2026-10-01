# -*- coding: utf-8 -*-
"""
Task Service - 定时任务

后台"定时任务"页里的第二种类型：把写好的任务（如国内摸鱼）从一个机器人推给一批接收者，
可以只执行一次，也可以每天定时执行。和提醒共用同一个 APScheduler，存在所属机器人的库里。
job_process 里要往外发消息的任务方法第一个参数是 bot_id，之后的参数（有的话）是接收者。
"""
import inspect
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any

import pytz
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .scheduler_service import MEMORY, get_job, get_jobs, scheduler

LOG = logging.getLogger("TaskService")

ID_PREFIX = "task_"
ONCE = "once"
DAILY = "daily"

TIMEZONES = {
    "Asia/Shanghai": "北京时间",
    "America/Chicago": "美中时间",
}


def _job_process():
    from ..jobs import job_process
    return job_process


def _find(job_name: str):
    """按方法名找 job_process 里的方法，找不到时抛 ValueError"""
    module = _job_process()
    func = getattr(module, job_name, None) if job_name and not job_name.startswith("_") else None
    if not inspect.isfunction(func) or func.__module__ != module.__name__:
        raise ValueError(f"找不到任务方法: {job_name}")
    return func


def _takes_bot(func) -> bool:
    """第一个参数叫 bot_id 的方法，执行时传入任务所属的机器人"""
    return next(iter(inspect.signature(func).parameters), None) == "bot_id"


def _takes_receiver(func) -> bool:
    """除了 bot_id 还有参数的方法按接收者逐个执行，没有的只执行一次"""
    return len(inspect.signature(func).parameters) > (1 if _takes_bot(func) else 0)


def _run_task(task_id: str, job_name: str, receivers: list[str], schedule: dict, bot_id: str) -> None:
    """APScheduler 触发函数（模块级，SQLAlchemy jobstore 按名字引用）"""
    LOG.info("定时任务触发: bot_id=%s task_id=%s job=%s receivers=%s", bot_id, task_id, job_name, receivers)
    func = _find(job_name)
    args = [bot_id] if _takes_bot(func) else []
    if not _takes_receiver(func):
        func(*args)
        return
    failed = []
    for index, receiver in enumerate(receivers):
        if index:
            time.sleep(30)
        try:
            func(*args, receiver)
        except Exception as e:
            LOG.exception("任务 %s 推送到 %s 失败", job_name, receiver)
            failed.append(f"{receiver}（{e}）")
    # 一个接收者失败不影响其他人，但整次执行要让调度器记成失败（会通知管理员）
    if failed:
        raise RuntimeError(f"任务 {job_name} 有 {len(failed)}/{len(receivers)} 个接收者推送失败: {', '.join(failed)}")


def _clean(job_name: str, receivers: Any, schedule: Any) -> tuple[str, list[str], dict, Any]:
    """校验表单，返回 (方法名, 接收者, 触发方式, APScheduler trigger)；不合法时抛 ValueError"""
    job_name = str(job_name or "").strip()
    func = _find(job_name)

    if not isinstance(receivers, list):
        raise ValueError("接收者必须是列表")
    receivers = list(dict.fromkeys(str(name).strip() for name in receivers if str(name).strip()))
    if not receivers and _takes_receiver(func):
        raise ValueError(f"{job_name} 需要接收者，至少填一个")

    schedule = schedule if isinstance(schedule, dict) else {}
    mode = schedule.get("mode")
    if mode == ONCE:
        import dateutil.parser
        try:
            run_at = dateutil.parser.isoparse(str(schedule.get("run_at", "")))
        except ValueError:
            raise ValueError("执行时间格式不对")
        if run_at.tzinfo is None:
            raise ValueError("执行时间必须带时区")
        if run_at <= datetime.now(run_at.tzinfo):
            raise ValueError("执行时间已经过去了")
        return job_name, receivers, {"mode": ONCE, "run_at": run_at.isoformat()}, DateTrigger(run_date=run_at)

    if mode == DAILY:
        at = str(schedule.get("time", "")).strip()
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", at)
        if not match:
            raise ValueError("每天执行的时间格式应为 HH:MM")
        timezone = schedule.get("timezone", "")
        if timezone not in TIMEZONES:
            raise ValueError(f"不支持的时区: {timezone}")
        hour, minute = int(match.group(1)), int(match.group(2))
        trigger = CronTrigger(hour=hour, minute=minute, timezone=pytz.timezone(timezone))
        return job_name, receivers, {"mode": DAILY, "time": f"{hour:02d}:{minute:02d}", "timezone": timezone}, trigger

    raise ValueError("触发方式只能是单次或每天")


def _schedule_job(bot_id: str, task_id: str, job_name: str, receivers: Any, schedule: Any) -> dict:
    job_name, receivers, schedule, trigger = _clean(job_name, receivers, schedule)
    job = scheduler.add_job(
        _run_task,
        trigger,
        id=task_id,
        jobstore=bot_id,
        replace_existing=True,
        max_instances=1,
        kwargs={"task_id": task_id, "job_name": job_name, "receivers": receivers, "schedule": schedule,
                "bot_id": bot_id},
    )
    return _describe(job)


def _describe(job) -> dict:
    kwargs = job.kwargs or {}
    return {
        "task_id": job.id,
        "job_name": kwargs.get("job_name", ""),
        "receivers": list(kwargs.get("receivers", [])),
        "schedule": dict(kwargs.get("schedule", {})),
        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else "",
    }


def _get(bot_id: str, task_id: str):
    job = get_job(bot_id, task_id) if task_id.startswith(ID_PREFIX) else None
    if not job:
        raise ValueError(f"未找到定时任务: {task_id}")
    return job


def job_names() -> list[str]:
    """job_process 里所有可以按名字执行的方法，后台下拉框用"""
    module = _job_process()
    return [name for name, func in vars(module).items()
            if not name.startswith("_") and inspect.isfunction(func) and func.__module__ == module.__name__]


def list_tasks(bot_id: str) -> list[dict]:
    """这个机器人的全部定时任务，按下次执行时间升序"""
    result = [_describe(job) for job in get_jobs(bot_id) if job.id.startswith(ID_PREFIX)]
    result.sort(key=lambda task: task["next_run_time"] or "9999")
    return result


def add_task(bot_id: str, job_name: str, receivers: Any, schedule: Any) -> dict:
    task_id = f"{ID_PREFIX}{job_name}_{uuid.uuid4().hex[:8]}"
    task = _schedule_job(bot_id, task_id, job_name, receivers, schedule)
    LOG.info("task/add: %s", task)
    return task


def update_task(bot_id: str, task_id: str, job_name: str, receivers: Any, schedule: Any) -> dict:
    _get(bot_id, task_id)
    task = _schedule_job(bot_id, task_id, job_name, receivers, schedule)
    LOG.info("task/update: %s", task)
    return task


def delete_task(bot_id: str, task_id: str) -> dict:
    _get(bot_id, task_id)
    scheduler.remove_job(task_id, jobstore=bot_id)
    LOG.info("task/delete: task_id=%s", task_id)
    return {"task_id": task_id}


def run_now(bot_id: str, task_id: str) -> dict:
    """立即执行一次，不影响之后的定时"""
    kwargs = {**(_get(bot_id, task_id).kwargs or {}), "bot_id": bot_id}
    _start(kwargs)
    LOG.info("task/run: task_id=%s", task_id)
    return {"task_id": task_id}


def run_by_job_name(bot_id: str, job_name: str) -> list[str]:
    """立即执行这个机器人在这个任务名下的所有定时任务（AI 手动触发用），返回执行了的 task_id"""
    _find(job_name)
    started = []
    for job in get_jobs(bot_id):
        kwargs = {**(job.kwargs or {}), "bot_id": bot_id}
        if job.id.startswith(ID_PREFIX) and kwargs.get("job_name") == job_name:
            _start(kwargs)
            started.append(job.id)
    LOG.info("task/run_by_job_name: bot_id=%s job=%s tasks=%s", bot_id, job_name, started)
    return started


def _start(kwargs: dict) -> None:
    """在调度器的线程池里执行，推送要几十秒，不阻塞接口"""
    scheduler.add_job(_run_task, kwargs=kwargs, jobstore=MEMORY)

