# -*- coding: utf-8 -*-
"""
PathResolver - 路径解析工具

Base 端的媒体目录都在工作目录下（uv 执行时工作目录就是 true-love-base）：
- wx_imgs/：收到的微信图片、视频、文件，通过 /media 开放给别的服务
- send-files/：要发出去的文件，从别的服务给的 URL 下载到这里

传给 Server 的是相对路径（如 wx_imgs/filename.jpg），Server 再拼成 base 的 /media URL。
"""

import logging
import os
from pathlib import Path

LOG = logging.getLogger("PathResolver")

# 微信图片下载目录名
WX_IMGS_DIR = "wx_imgs"
# 要发出去的文件的下载目录
SEND_FILES_DIR = Path("send-files")


def get_wx_imgs_dir() -> str:
    """
    获取 wx_imgs 文件夹路径，不存在则创建

    Returns:
        wx_imgs 文件夹路径
    """
    if not os.path.exists(WX_IMGS_DIR):
        os.makedirs(WX_IMGS_DIR)
        LOG.info(f"Created wx_imgs directory: {WX_IMGS_DIR}")

    return WX_IMGS_DIR


def to_server_path(full_path: str) -> str:
    """
    将完整路径转换为传给 Server 的相对路径

    Args:
        full_path: 完整文件路径，如 "wx_imgs/xxx.jpg"

    Returns:
        相对路径，如 "wx_imgs/xxx.jpg"
    """
    filename = os.path.basename(str(full_path))
    relative_path = f"{WX_IMGS_DIR}/{filename}"
    LOG.debug(f"Converted to server path: {full_path} -> {relative_path}")
    return relative_path
