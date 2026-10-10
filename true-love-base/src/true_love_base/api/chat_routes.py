# -*- coding: utf-8 -*-
"""
tl-admin 聊天页和好友页的接口，server 转发过来，直接操作这个号的微信

    POST /chat/sessions       会话列表
    POST /chat/messages       读一个会话的消息
    POST /chat/quote          引用一条消息回复
    POST /chat/tickle         拍一拍一条消息的发送人
    POST /chat/media          下载一条图片、视频、文件消息，文件通过 /media 开放
    POST /friends/requests    新的朋友
    POST /friends/accept      通过好友申请
    POST /friends/add         加好友
    POST /friends/edit        改好友备注

发文字和文件用 /send/text、/send/file。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Body
from starlette.concurrency import run_in_threadpool

from true_love_base.api.routes import _get_robot, _payload, _unavailable
from true_love_base.models.api import ApiErrors, ApiResponse

LOG = logging.getLogger("BaseChatRoutes")

router = APIRouter()

# 往上翻历史一次最多多少条
HISTORY_MAX = 100
_FAILED = 107


async def _run(label: str, action: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """在线程池里操作微信；做不到的原因原样告诉后台"""
    try:
        return ApiResponse.success(await run_in_threadpool(action, *args, **kwargs)).to_dict()
    except (LookupError, ValueError) as e:
        LOG.warning("%s: %s", label, e)
        return ApiResponse.error(_FAILED, str(e)).to_dict()
    except Exception as e:
        LOG.exception("%s failed", label)
        return ApiResponse.error(_FAILED, f"{label} failed: {e}").to_dict()


@router.post("/chat/sessions")
async def sessions(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    Response:
        - data: {"sessions": [{"name", "time", "content", "new_count", "ismute", "listening"}]}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    return await _run("Reading sessions", lambda: {"sessions": robot.client.sessions()})


@router.post("/chat/messages")
async def messages(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    Request Body:
        - chat_name: 会话名
        - history: 先往上翻这么多条更早的消息，可选

    Response:
        - data: {"chat_name", "chat_type", "member_count", "messages": [{"id", "kind", "type", "sender", "content"}]}
          kind 是 self（自己发的）、friend（别人发的）、time（时间）或 system（系统提示）
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    chat_name = str(data.get("chat_name") or "")
    try:
        history = max(0, min(int(data.get("history") or 0), HISTORY_MAX))
    except (TypeError, ValueError):
        return ApiErrors.INVALID_PARAMS.to_dict()
    if not chat_name:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run(f"Reading [{chat_name}]", robot.read_chat, chat_name, history)


@router.post("/chat/quote")
async def quote(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Request Body: chat_name、msg_id（读消息时给的 id）、content"""
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    chat_name, msg_id, content = (str(data.get(key) or "") for key in ("chat_name", "msg_id", "content"))
    if not chat_name or not msg_id or not content.strip():
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run(f"Quoting in [{chat_name}]", robot.client.quote_message, chat_name, msg_id, content)


@router.post("/chat/tickle")
async def tickle(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Request Body: chat_name、msg_id"""
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    chat_name, msg_id = (str(data.get(key) or "") for key in ("chat_name", "msg_id"))
    if not chat_name or not msg_id:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run(f"Tickling in [{chat_name}]", robot.client.tickle_message, chat_name, msg_id)


@router.post("/chat/media")
async def media(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    Request Body:
        - chat_name、msg_id
        - quoted: 下载这条消息引用的图片或视频，可选

    Response:
        - data: {"path": 相对路径，GET /media/{path} 取文件}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    chat_name, msg_id = (str(data.get(key) or "") for key in ("chat_name", "msg_id"))
    if not chat_name or not msg_id:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run(f"Downloading from [{chat_name}]", lambda: {"path": robot.client.download_message(
        chat_name, msg_id, bool(data.get("quoted")))})


@router.post("/friends/requests")
async def friend_requests(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    Response:
        - data: {"requests": [{"content", "acceptable"}]}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    return await _run("Reading friend requests", lambda: {"requests": robot.client.friend_requests()})


@router.post("/friends/accept")
async def accept_friend(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Request Body: content（申请条目上的文字）、remark"""
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    content = str(data.get("content") or "")
    if not content:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run("Accepting a friend request", robot.client.accept_friend, content,
                      str(data.get("remark") or ""))


@router.post("/friends/add")
async def add_friend(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    Request Body: keywords（微信号或手机号）、addmsg、remark

    Response:
        - data: {"message": SDK 给的结果说明}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    keywords = str(data.get("keywords") or "").strip()
    if not keywords:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run("Adding a friend", lambda: {"message": robot.client.add_friend(
        keywords, str(data.get("addmsg") or ""), str(data.get("remark") or ""))})


@router.post("/friends/edit")
async def edit_friend(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Request Body: chat_name、remark"""
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    data = _payload(request)
    chat_name, remark = (str(data.get(key) or "").strip() for key in ("chat_name", "remark"))
    if not chat_name or not remark:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return await _run(f"Editing friend [{chat_name}]", robot.client.edit_friend, chat_name, remark)
