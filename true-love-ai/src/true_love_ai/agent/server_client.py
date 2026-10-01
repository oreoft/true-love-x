# -*- coding: utf-8 -*-
"""
Server Callback Client

AI Agent 执行完后，通过这个客户端调用 Server 的 /action/* 业务接口
来完成聊天操作（发消息、管理提醒、管理监听、查聊天记录等）。

所有机器人共用一个 server。每个请求都带上这次处理的消息所属的 bot_id，server 按它找到机器人的 base 和库；
不在处理消息时（如启动通知）不带 bot_id，server 用默认机器人。AI 不用关心机器人是哪个平台。
"""

import contextvars
import logging
from contextlib import contextmanager

from true_love_common.hosts import server_host
from true_love_common.http.client import async_get, async_post_json, post_json

from true_love_ai.core.config import get_config

LOG = logging.getLogger("ServerClient")


# 当前这次处理的消息来自哪个机器人；不在处理消息时为空，server 用默认机器人
_bot_id: contextvars.ContextVar[str] = contextvars.ContextVar("bot_id", default="")


@contextmanager
def replying_for(bot_id: str):
    """这段代码里（包括它派生的任务）的回调都以 bot_id 这个机器人的身份发出"""
    token = _bot_id.set(bot_id)
    try:
        yield
    finally:
        _bot_id.reset(token)


def _with_bot(payload: dict) -> dict:
    """补上 token 和当前机器人"""
    payload["token"] = _get_token()
    bot_id = _bot_id.get()
    if bot_id:
        payload["bot_id"] = bot_id
    return payload


def _get_token() -> str:
    config = get_config()
    return config.http.token[0] if config.http and config.http.token else ""


def _post(path: str, payload: dict, timeout: float = 10.0) -> dict:
    """同步 POST"""
    url = f"{server_host()}{path}"
    result = post_json(url, _with_bot(payload), timeout=timeout)
    if result.ok:
        return result.data if isinstance(result.data, dict) else {}
    LOG.error("Server callback failed path=%s error=%s", path, result.error or result.text)
    return {"code": -1, "msg": result.error or result.text}


async def _async_post(path: str, payload: dict, timeout: float = 10.0) -> dict:
    """异步 POST"""
    url = f"{server_host()}{path}"
    result = await async_post_json(url, _with_bot(payload), timeout=timeout)
    if result.ok:
        return result.data if isinstance(result.data, dict) else {}
    LOG.error("Server callback failed path=%s error=%s", path, result.error or result.text)
    return {"code": -1, "msg": result.error or result.text}


# ==================== 消息发送 ====================

def notify_master_sync(content: str) -> bool:
    """同步给管理员发通知（用于启动/关闭等非异步场景）。管理员是谁由 base 决定，这里不传接收者"""
    result = _post("/action/send", {"is_master": True, "content": content})
    return result.get("code") == 0


async def send_text(receiver: str, content: str, at_user: str = "", reply_msg_id: str = "") -> bool:
    """reply_msg_id: 这条是在回复哪条消息，base 按群回复方式设置决定 @、拍一拍还是引用"""
    payload = {"receiver": receiver, "content": content, "at_user": at_user}
    if reply_msg_id:
        payload["reply_msg_id"] = reply_msg_id
    result = await _async_post("/action/send", payload)
    return result.get("code") == 0


async def send_file(receiver: str, path: str) -> bool:
    """
    通知 Server 发送 AI 生成的文件。

    path: AI 本地相对路径，如 gen_img/abc123.jpg、gen_video/abc123.mp4
          Server 会拼成 AI 的 /media URL 交给 base 下载。
    """
    result = await _async_post("/action/send-file", {
        "receiver": receiver, "path": path,
    }, timeout=60.0)
    return result.get("code") == 0


# ==================== 提醒管理 ====================

async def add_reminder(job_id: str, target_time_iso: str, receiver: str,
                       content: str, at_user: str = "") -> dict:
    return await _async_post("/action/reminder/add", {
        "job_id": job_id, "target_time_iso": target_time_iso,
        "receiver": receiver, "at_user": at_user, "content": content,
    })


async def delete_reminder(job_id: str) -> bool:
    result = await _async_post("/action/reminder/delete", {"job_id": job_id})
    return result.get("code") == 0


async def update_reminder(job_id: str, new_time_iso: str = "",
                          new_content: str = "") -> dict:
    return await _async_post("/action/reminder/update", {
        "job_id": job_id,
        "new_time_iso": new_time_iso,
        "new_content": new_content,
    })


async def query_reminders(receiver: str) -> list[dict]:
    result = await _async_post("/action/reminder/query", {
        "receiver": receiver,
    })
    return result.get("data", {}).get("jobs", [])


# ==================== 监听管理 ====================

async def listen_add(chat_name: str) -> dict:
    return await _async_post("/action/listen/add", {"chat_name": chat_name})


async def listen_remove(chat_name: str) -> dict:
    return await _async_post("/action/listen/remove", {"chat_name": chat_name})


# ==================== 媒体文件获取 ====================

async def fetch_media_bytes(ref: str, timeout: float = 15.0) -> bytes | None:
    """拉取入站媒体原始字节。server 转来的消息里，媒体已经换成了收到它的 base 上的 URL。"""
    if not ref.startswith(("http://", "https://")):
        LOG.error("fetch_media_bytes: 无法下载的媒体引用 ref=%s", ref)
        return None
    result = await async_get(ref, timeout=timeout)
    if not result.ok:
        LOG.error("fetch_media_bytes 失败: ref=%s err=%s", ref, result.error or result.text)
        return None
    ct = result.headers.get("content-type", "")
    if "application/json" in ct or "text/" in ct:
        LOG.error("fetch_media_bytes 返回非媒体内容: ref=%s content-type=%s", ref, ct)
        return None
    return result.content


# ==================== 历史记录查询 ====================

async def query_history(chat_id: str, sender_id: str = "", sender_name: str = "",
                        limit: int = 500) -> list[dict]:
    """查询当前机器人的聊天历史，sender_id / sender_name 均为可选过滤条件"""
    payload = {"chat_id": chat_id, "limit": limit}
    if sender_id:
        payload["sender_id"] = sender_id
    if sender_name:
        payload["sender_name"] = sender_name
    result = await _async_post("/action/history", payload, timeout=20.0)
    return result.get("data", {}).get("messages", [])
