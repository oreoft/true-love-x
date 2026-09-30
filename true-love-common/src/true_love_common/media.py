# -*- coding: utf-8 -*-
"""
服务之间传媒体文件

每个服务用 /media 开放自己的目录，别的服务拿到 URL 后自己下载，不再共用本地目录。
消息和数据库里存的是文件所在服务的相对路径（如 wx_imgs/a.jpg），发给别的服务前用 to_url 换成 URL。
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from true_love_common.chat_msg import ChatMsg
from true_love_common.http.client import async_get

LOG = logging.getLogger("Media")


def media_router(dirs: list[Path]) -> APIRouter:
    """
    GET /media/{目录}/{文件名}：只开放 dirs 里的目录，不做鉴权，只在内网暴露

    Args:
        dirs: 开放的目录，相对服务的工作目录，URL 里用目录名访问
    """
    allowed = {d.name: d for d in dirs}
    router = APIRouter()

    @router.get("/media/{path:path}")
    async def serve_media(path: str) -> FileResponse:
        dir_name, _, filename = path.lstrip("/").partition("/")
        base_dir = allowed.get(dir_name)
        if base_dir is None or not filename:
            raise HTTPException(status_code=404, detail="文件不存在")
        root = base_dir.resolve()
        file_path = (root / filename).resolve()
        if not file_path.is_relative_to(root):
            raise HTTPException(status_code=403, detail="非法路径")
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(path=file_path, filename=file_path.name)

    return router


def to_url(ref: str, host: str) -> str:
    """相对路径拼成 host 上的 /media URL，已经是 URL 的原样返回"""
    if ref.startswith(("http://", "https://")):
        return ref
    return f"{host.rstrip('/')}/media/{ref.lstrip('/')}"


def attach_urls(msg: ChatMsg, host: str) -> None:
    """把消息（含引用的消息）里的相对路径换成 host 上的 URL"""
    for part in (msg.image_msg, msg.voice_msg, msg.video_msg, msg.file_msg):
        resource = part.resource if part else None
        if resource and resource.ref:
            resource.ref = to_url(resource.ref, host)
            resource.source = "http"
    if msg.refer_msg:
        attach_urls(msg.refer_msg, host)


async def download(url: str, save_dir: Path) -> str:
    """下载到 save_dir，文件名沿用 URL 里的，返回相对工作目录的路径"""
    filename = Path(unquote(urlparse(url).path)).name or "download.bin"
    save_dir.mkdir(parents=True, exist_ok=True)
    result = await async_get(url, timeout=(10, 60))
    result.raise_for_status()
    file_path = save_dir / filename
    file_path.write_bytes(result.content)
    LOG.info("下载媒体文件: %s -> %s (%d bytes)", url, file_path.as_posix(), len(result.content))
    return file_path.as_posix()
