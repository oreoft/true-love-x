#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 应用模块
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from true_love_common.integrations.fastapi import HttpLoggingMiddleware, setup_exception_handlers
from true_love_common.media import media_router

from true_love_ai.api.admin_routes import admin_router
from true_love_ai.api.data_routes import data_router
from true_love_ai.api.trigger_routes import trigger_router
from true_love_ai.services.audio_service import GEN_AUDIO_DIR
from true_love_ai.services.image_service import GEN_IMG_DIR
from true_love_ai.services.video_service import GEN_VIDEO_DIR

LOG = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（数据库在 main 里启动前就初始化好了）"""
    LOG.info("tl-ai 服务启动成功...")
    yield
    LOG.info("tl-ai 服务关闭中...")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""

    application = FastAPI(
        title="True Love AI",
        description="tl-ai：所有机器人共用的 AI 服务",
        version="0.2.0",
        lifespan=lifespan
    )

    # CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.add_middleware(
        HttpLoggingMiddleware,
        service_name="tl-ai",
        skip_paths={"/health"},
        max_response_body_chars=200,
    )
    setup_exception_handlers(application, internal_message="发生未知错误, 稍后再试试捏")

    # 注册路由，按调用方分：
    # - 给 server 的业务接口：/trigger（转交消息）、/data/*（定时任务要的数据）
    # - 给 server 转发的 tl-admin 管理接口：/admin/*
    # - /media：AI 生成的图片、视频、音频，base 下载后发送
    application.include_router(trigger_router)
    application.include_router(data_router)
    application.include_router(admin_router)
    application.include_router(media_router([GEN_IMG_DIR, GEN_VIDEO_DIR, GEN_AUDIO_DIR]))

    # 健康检查
    @application.get("/")
    async def root():
        return "pong"

    @application.get("/ping")
    async def ping():
        return "pong"

    @application.get("/health")
    async def health():
        """健康检查端点，供 Docker/K8s 使用"""
        return {"status": "healthy", "service": "true-love-ai"}

    return application


# 创建应用实例
app = create_app()
