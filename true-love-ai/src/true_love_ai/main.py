#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
True Love AI 主入口
"""
import logging
import signal

import uvicorn

from true_love_ai.core.config import get_config
from true_love_ai.agent.server_client import notify_master_sync
from true_love_ai.llm.llm_bootstrap import init_llm

LOG = logging.getLogger("Main")


def _notify_master(content: str) -> None:
    """给管理员发通知；发不出去只记日志，不影响启动和退出"""
    try:
        if not notify_master_sync(content):
            LOG.warning("管理员通知没发出去: %s", content)
    except Exception as e:
        LOG.warning("管理员通知发送异常: content=%s err=%s", content, e, exc_info=True)


def notice_master():
    """启动通知"""
    _notify_master("tl-ai 启动成功")


def setup_signal_handlers():
    """设置信号处理"""

    def handler(sig, frame):
        LOG.info("收到关闭信号，正在退出...")
        _notify_master("tl-ai 正在关闭...")
        exit(0)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def main():
    """主入口"""
    # 加载配置（日志已在 config 模块初始化时配置）
    config = get_config()

    # 初始化数据库（人设、技能权限、模型都存在库里，后面的初始化要用）
    from true_love_ai.core.db_engine import init_db
    init_db()

    # 初始化 LLM 客户端
    init_llm()

    # 加载所有 AI 本地 skills
    from true_love_ai.agent import skill_registry
    from true_love_ai.agent.skills import ensure_skills_loaded
    from true_love_ai.memory import skill_access_service
    ensure_skills_loaded()
    # 新加的内置技能第一次进权限表，拿后台设的默认权限点
    skill_access_service.sync_builtin(skill_registry.names())
    LOG.info("AI 本地 skills 加载完成")

    # 设置信号处理
    setup_signal_handlers()

    # 启动通知
    notice_master()

    LOG.info("=" * 50)
    LOG.info("tl-ai 服务启动中...")
    LOG.info("版本: 0.2.0")
    LOG.info("=" * 50)

    # 获取 HTTP 配置
    http_config = config.http
    if not http_config:
        LOG.error("HTTP 配置缺失，无法启动服务")
        return

    # 启动 FastAPI 服务
    uvicorn.run(
        "true_love_ai.api.app:app",
        host=http_config.host,
        port=http_config.port,
        reload=False,
        log_level="info",
        access_log=False  # 使用自定义日志中间件
    )


if __name__ == "__main__":
    main()
