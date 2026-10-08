# -*- coding: utf-8 -*-
"""
FastAPI Application - FastAPI 应用

创建和配置 FastAPI 应用实例。路由按调用方分文件：

- open_routes：外部调用方（/send-msg、/ping、/health）
- base_routes：各平台的 base（/base/*）
- ai_routes：AI 的业务回调（/action/*）
- admin_bot_routes、admin_platform_routes、admin_ai_routes：tl-admin（/admin/*），页面本身是 /admin；
  admin_ai_routes 是存在 AI 那边的设置，原样转发给 AI
- /media：server 自己的图片（摸鱼图、早报图），base 下载后发送
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from true_love_common.integrations.fastapi import HttpLoggingMiddleware

from .admin_ai_routes import admin_ai_router
from .admin_bot_routes import admin_bot_router
from .admin_platform_routes import admin_platform_router
from .ai_routes import ai_router
from .base_routes import base_router
from .exception_handlers import setup_exception_handlers
from .open_routes import open_router
from ..services import bot_registry
from ..services.listen_manager import get_listen_manager

LOG = logging.getLogger("FastAPIApp")


# server 启动后等多久再补一次监听，给同时启动的 base 留出自己注册监听的时间
STARTUP_REFRESH_DELAY = 60


async def _refresh_listen_after_startup():
    """兜底：base 比 server 先起来、等不到 server 放弃监听时，由 server 把每个微信机器人的监听补上"""
    await asyncio.sleep(STARTUP_REFRESH_DELAY)
    for bot in bot_registry.list_all():
        if not bot.can("listen"):
            continue
        try:
            result = await get_listen_manager(bot.bot_id).refresh_listen()
            log = LOG.error if result["fail_count"] > 0 else LOG.info
            log("启动后补监听完成: bot_id=%s 共 %s 个，失败 %s 个", bot.bot_id, result["total"], result["fail_count"])
        except Exception:
            LOG.exception("启动后补监听失败: bot_id=%s", bot.bot_id)


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
        description="tl-server：多个机器人共用的中转层，连接各平台的 base 和 AI",
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
    app.include_router(open_router)
    app.include_router(base_router)
    app.include_router(ai_router)
    app.include_router(admin_bot_router)
    app.include_router(admin_platform_router)
    app.include_router(admin_ai_router)

    # /admin 路径返回 tl-admin 页面
    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return FileResponse("static/index.html")

    # 挂载静态文件目录（放在最后，避免覆盖其他路由）
    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app
