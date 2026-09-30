# -*- coding: utf-8 -*-
"""
FastAPI Application - FastAPI 应用

创建和配置 FastAPI 应用实例。
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from true_love_common.integrations.fastapi import HttpLoggingMiddleware
from true_love_common.media import media_router

from .routes import router
from .action_routes import action_router
from .exception_handlers import setup_exception_handlers
from ..jobs.job_process import MEDIA_DIRS
from ..services.listen_manager import get_listen_manager

LOG = logging.getLogger("FastAPIApp")


# server 启动后等多久再补一次监听，给同时启动的 base 留出自己注册监听的时间
STARTUP_REFRESH_DELAY = 60


async def _refresh_listen_after_startup():
    """兜底：base 比 server 先起来、等不到 server 放弃监听时，由 server 把监听补上"""
    await asyncio.sleep(STARTUP_REFRESH_DELAY)
    try:
        result = await get_listen_manager().refresh_listen()
        LOG.info("启动后补监听完成: 共 %s 个，失败 %s 个", result["total"], result["fail_count"])
    except Exception:
        LOG.exception("启动后补监听失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    LOG.info("FastAPI 应用已启动...")
    refresh = asyncio.create_task(_refresh_listen_after_startup())
    yield
    refresh.cancel()
    LOG.info("FastAPI 应用已关闭...")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="True Love Server",
        description="真爱粉服务端 - 微信机器人后端服务",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        HttpLoggingMiddleware,
        service_name="tl-server",
        skip_paths={"/health", "/ping", "/admin", "/static"},
        max_response_body_chars=200,
    )

    # 设置异常处理器
    setup_exception_handlers(app)

    # 注册路由
    app.include_router(router)
    app.include_router(action_router)
    app.include_router(media_router(MEDIA_DIRS))

    # /admin 路径返回管理页面
    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return FileResponse("static/index.html")

    # 挂载静态文件目录（放在最后，避免覆盖其他路由）
    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app
