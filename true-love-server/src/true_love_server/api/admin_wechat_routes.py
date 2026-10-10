# -*- coding: utf-8 -*-
"""
Admin WeChat Routes - tl-admin 的聊天页和好友页（/admin/bots/{bot_id}/wechat/*）

直接操作机器人登录的微信：会话列表、读消息、发文字和文件、拍一拍、引用；好友申请、加好友、改备注。
数据都是现场从微信界面读的，server 只转发，不存。只有微信机器人有这些功能。
tl-admin 只在内网暴露，这些接口不校验 token。
"""

import logging
import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Request, Response
from true_love_common import r2

from . import deps
from .exception_handlers import ApiResponse, ValidationException
from ..core import Config
from ..services import base_client

LOG = logging.getLogger("AdminWeChatRoutes")

admin_wechat_router = APIRouter(prefix="/admin/bots/{bot_id}/wechat")

# 往上翻历史一次最多加载多少条
HISTORY_MAX = 100
# 后台上传的文件先存到 server 再传 R2，太大的不收
UPLOAD_MAX_BYTES = 100 * 1024 * 1024


def _wechat(bot_id: str) -> base_client.WeChatClient:
    return base_client.wechat(deps.wechat_bot(bot_id).bot_id)


def _required(request: dict, *keys: str) -> list[str]:
    values = [str(request.get(key) or "").strip() for key in keys]
    missing = [key for key, value in zip(keys, values) if not value]
    if missing:
        raise ValidationException(f"{'、'.join(missing)} 不能为空")
    return values


def _data(result: dict, failure: str):
    """base 的结果：失败时把 base 给的原因告诉后台"""
    if not result.get("success"):
        raise ValidationException(f"{failure}：{result.get('message') or '机器人没有回应'}")
    return result.get("data")


# ==================== 聊天 ====================

@admin_wechat_router.get("/sessions")
async def sessions(bot_id: str):
    """微信主窗口的会话列表，和微信里的顺序一样"""
    return ApiResponse(data=_data(await _wechat(bot_id).sessions(), "读会话列表失败"))


@admin_wechat_router.get("/messages")
async def messages(bot_id: str, chat_name: str, history: int = 0):
    """
    一个会话里现在能看到的消息，从微信界面现场读

    Query:
        - chat_name: 会话名
        - history:   先往上翻这么多条更早的消息再读，0 是只读当前能看到的
    """
    if not chat_name.strip():
        raise ValidationException("chat_name 不能为空")
    history = max(0, min(history, HISTORY_MAX))
    return ApiResponse(data=_data(await _wechat(bot_id).chat_messages(chat_name, history), "读消息失败"))


@admin_wechat_router.post("/send-text")
async def send_text(bot_id: str, request: dict):
    """
    Body:
        - chat_name: 发给谁
        - content:   内容
        - at:        群里要 @ 的人，可选
    """
    chat_name, content = _required(request, "chat_name", "content")
    at = [name for name in request.get("at") or [] if name]
    ok, message = await _wechat(bot_id).send_text(chat_name, at, content)
    if not ok:
        raise ValidationException(f"发送失败：{message}")
    return ApiResponse(data=None)


@admin_wechat_router.post("/send-file")
async def send_file(bot_id: str, chat_name: str, filename: str, request: Request):
    """
    发文件，请求体就是文件内容；先传到 R2，base 用预签名链接下载后发出去

    Query:
        - chat_name: 发给谁
        - filename:  文件名，微信里显示的就是它
    """
    client = _wechat(bot_id)
    name = Path(filename).name.strip()
    if not chat_name.strip() or not name:
        raise ValidationException("chat_name、filename 不能为空")
    body = await request.body()
    if not body:
        raise ValidationException("文件是空的")
    if len(body) > UPLOAD_MAX_BYTES:
        raise ValidationException(f"文件不能超过 {UPLOAD_MAX_BYTES // 1024 // 1024}MB")
    with tempfile.TemporaryDirectory(prefix="tl-admin-upload-") as folder:
        path = Path(folder) / name
        path.write_bytes(body)
        try:
            url = await r2.upload(r2.R2Config.from_dict(Config().R2), path, "admin")
        except Exception as e:
            LOG.error("后台上传的文件传 R2 失败: %s", name, exc_info=True)
            raise ValidationException(f"上传文件失败：{e}")
    ok, message = await client.send_file(url, chat_name)
    if not ok:
        raise ValidationException(f"发送失败：{message}")
    return ApiResponse(data=None)


@admin_wechat_router.get("/media")
async def media(bot_id: str, chat_name: str, msg_id: str, quoted: bool = False):
    """
    点开一条图片、视频或文件消息：机器人在微信里下载下来，原样转给后台

    Query:
        - chat_name、msg_id: 读消息时给的
        - quoted: 取这条消息引用的那张图片或视频
    """
    if not chat_name.strip() or not msg_id.strip():
        raise ValidationException("chat_name、msg_id 不能为空")
    data = _data(await _wechat(bot_id).download_media(chat_name, msg_id, quoted), "加载失败")
    name = data["name"]
    return Response(content=data["content"],
                    media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{url_quote(name)}"})


@admin_wechat_router.post("/quote")
async def quote(bot_id: str, request: dict):
    """引用一条消息回复。Body: chat_name、msg_id（读消息时给的 id）、content"""
    chat_name, msg_id, content = _required(request, "chat_name", "msg_id", "content")
    _data(await _wechat(bot_id).quote(chat_name, msg_id, content), "引用失败")
    return ApiResponse(data=None)


@admin_wechat_router.post("/tickle")
async def tickle(bot_id: str, request: dict):
    """拍一拍一条消息的发送人。Body: chat_name、msg_id"""
    chat_name, msg_id = _required(request, "chat_name", "msg_id")
    _data(await _wechat(bot_id).tickle(chat_name, msg_id), "拍一拍失败")
    return ApiResponse(data=None)


# ==================== 好友 ====================

@admin_wechat_router.get("/friend-requests")
async def friend_requests(bot_id: str):
    """通讯录「新的朋友」里的申请，只读，不会通过"""
    return ApiResponse(data=_data(await _wechat(bot_id).friend_requests(), "读好友申请失败"))


@admin_wechat_router.post("/friend-accept")
async def friend_accept(bot_id: str, request: dict):
    """
    通过一条好友申请

    Body:
        - content: 申请条目上的文字（读申请时给的），用来找到这一条
        - remark:  备注，可选
    """
    [content] = _required(request, "content")
    payload = {"content": content, "remark": str(request.get("remark") or "").strip()}
    return ApiResponse(data=_data(await _wechat(bot_id).accept_friend(payload), "通过好友申请失败"))


@admin_wechat_router.post("/friend-add")
async def friend_add(bot_id: str, request: dict):
    """
    搜索微信号或手机号，发好友申请

    Body:
        - keywords: 微信号或手机号
        - addmsg:   验证消息，可选
        - remark:   备注，可选
    """
    [keywords] = _required(request, "keywords")
    payload = {"keywords": keywords, "addmsg": str(request.get("addmsg") or "").strip(),
               "remark": str(request.get("remark") or "").strip()}
    return ApiResponse(data=_data(await _wechat(bot_id).add_friend(payload), "加好友失败"))


@admin_wechat_router.post("/friend-edit")
async def friend_edit(bot_id: str, request: dict):
    """
    改好友的备注（标签不做：SDK 改标签在 ser 上找不到标签控件）

    Body:
        - chat_name: 好友的会话名
        - remark:    新备注
    """
    chat_name, remark = _required(request, "chat_name", "remark")
    payload = {"chat_name": chat_name, "remark": remark}
    return ApiResponse(data=_data(await _wechat(bot_id).edit_friend(payload), "修改备注失败"))
