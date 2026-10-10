# -*- coding: utf-8 -*-
"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from true_love_common.integrations.fastapi import HttpLoggingMiddleware, setup_exception_handlers
from true_love_common.media import media_router

from true_love_base.api.chat_routes import router as chat_router
from true_love_base.api.routes import router
from true_love_base.utils.path_resolver import WX_IMGS_DIR


def create_app() -> FastAPI:
    application = FastAPI(title="True Love Base", version="0.2.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(
        HttpLoggingMiddleware,
        service_name="tl-base",
        skip_paths={"/ping"},
        max_response_body_chars=200,
    )
    setup_exception_handlers(application)
    application.include_router(router)
    # tl-admin 的聊天页和好友页
    application.include_router(chat_router)
    # 收到的微信文件开放给 server 和 AI 下载
    application.include_router(media_router([Path(WX_IMGS_DIR)]))
    return application


app = create_app()
