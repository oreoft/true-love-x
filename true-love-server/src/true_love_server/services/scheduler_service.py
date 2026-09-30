# -*- coding: utf-8 -*-
"""
Scheduler Service - 定时任务调度服务

所有机器人共用一个 APScheduler。每个机器人的提醒和定时任务存在它自己的库里：
调度器给每个机器人挂一个 SQLAlchemyJobStore，别名就是 bot_id，增删查任务时都要指明 jobstore。
"立即执行"这种一次性的放内存。
"""
import logging

from apscheduler import events
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.base import ConflictingIdError
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from ..core import db_engine

LOG = logging.getLogger("SchedulerService")

MEMORY = "memory"

_executors = {
    'default': ThreadPoolExecutor(20)
}
_job_defaults = {
    'coalesce': False,
    'max_instances': 3,
    'misfire_grace_time': 3600  # 允许最多 1 小时的延迟（容错执行）
}

# 全局单例调度器
scheduler = BackgroundScheduler(
    jobstores={MEMORY: MemoryJobStore()},
    executors=_executors,
    job_defaults=_job_defaults,
    timezone='UTC'  # 底层全都走 UTC 标准记录，触发时再处理
)


def _scheduler_listener(event):
    """调度器事件监听，用于记录详细的任务执行状态"""
    if event.code == events.EVENT_JOB_EXECUTED:
        LOG.info("Job [%s] in [%s] executed successfully.", event.job_id, event.jobstore)
    elif event.code == events.EVENT_JOB_ERROR:
        LOG.error("Job [%s] in [%s] failed with exception: %s", event.job_id, event.jobstore, event.exception)
    elif event.code == events.EVENT_JOB_MISSED:
        LOG.warning("Job [%s] in [%s] was MISSED (scheduled run time was %s). "
                    "It will be retried if within grace time.",
                    event.job_id, event.jobstore, event.scheduled_run_time)


scheduler.add_listener(_scheduler_listener,
                       events.EVENT_JOB_EXECUTED | events.EVENT_JOB_ERROR | events.EVENT_JOB_MISSED)


def add_bot_store(bot_id: str) -> None:
    """
    给机器人挂上它库里的任务存储，并把合并前存的任务补上 bot_id（机器人的库打开后调用，调度器要已经启动）

    挂上和补 bot_id 之间暂停调度，免得到期的老任务带着旧参数先跑起来。
    """
    store = SQLAlchemyJobStore(engine=db_engine.bot_engine(bot_id), tablename='apscheduler_jobs')
    scheduler.pause()
    try:
        scheduler.add_jobstore(store, alias=bot_id)
        _stamp_bot_id(bot_id)
    except ValueError:
        LOG.debug("Job store for bot [%s] already added", bot_id)
        return
    finally:
        scheduler.resume()
    LOG.info("Job store for bot [%s] added", bot_id)


def _stamp_bot_id(bot_id: str) -> None:
    """合并前每台 server 只服务一个机器人，存的任务参数里没有 bot_id，还带着不再使用的 platform"""
    for job in scheduler.get_jobs(jobstore=bot_id):
        kwargs = dict(job.kwargs or {})
        if kwargs.get("bot_id") == bot_id and "platform" not in kwargs:
            continue
        kwargs.pop("platform", None)
        kwargs["bot_id"] = bot_id
        try:
            job.modify(kwargs=kwargs)
            LOG.info("Job [%s] of bot [%s] stamped with its bot_id", job.id, bot_id)
        except ConflictingIdError:
            LOG.warning("Job [%s] of bot [%s] could not be stamped", job.id, bot_id)


def get_job(bot_id: str, job_id: str):
    """这个机器人的任务，没有时返回 None"""
    return scheduler.get_job(job_id, jobstore=bot_id)


def get_jobs(bot_id: str) -> list:
    return scheduler.get_jobs(jobstore=bot_id)


def start_scheduler():
    """启动调度器；机器人的任务存储在各自的库打开后再挂上"""
    if not scheduler.running:
        scheduler.start()
        LOG.info("Global persistent APScheduler has started.")
