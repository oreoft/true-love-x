# -*- coding: utf-8 -*-
"""HTTP routes for true-love-base."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

from fastapi import APIRouter, Body
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from true_love_base.models.reply import REPLY_STYLES
from true_love_base.models.api import ApiErrors, ApiResponse
from true_love_common.media import download

from true_love_base.utils.path_resolver import SEND_FILES_DIR

if TYPE_CHECKING:
    from true_love_base.services.robot import Robot

LOG = logging.getLogger("BaseApiRoutes")
T = TypeVar("T")

MAX_CHUNK_BYTES = 2000
METHOD_BLACKLIST = {
    "ShutDown",  # 危险：会杀掉微信进程
    "KeepRunning",  # 阻塞方法，不应通过 API 调用
    "StartListening",  # 阻塞方法
    "StopListening",  # 可能影响正常监听
    "AddListenChat",  # 需要 callback，使用 /listen/add 独立接口
}

router = APIRouter()


@router.get("/", response_class=PlainTextResponse)
async def root() -> str:
    """健康检查"""
    return "pong"


@router.get("/ping", response_class=PlainTextResponse)
async def ping() -> str:
    """健康检查"""
    return "pong"


@router.get("/status")
async def status() -> dict[str, Any]:
    """
    微信连接状态

    base 进程不依赖微信存活，微信是否可用要看这个接口。

    Response:
        - data: {"wx_online": 微信是否在线, "self_name": 当前登录的昵称, "since": 进入当前状态的时间,
                  "bot_id": 这个机器人的标识（wxid）}
    """
    robot = _get_robot()
    if robot is None:
        return ApiErrors.ROBOT_NOT_READY.to_dict()
    return ApiResponse.success(robot.client.status()).to_dict()


@router.post("/send/text")
async def send_text(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    发送文本消息

    Request Body:
        - sendReceiver: 接收者
        - is_master: 为 true 时发给这个号的管理员，忽略 sendReceiver（可选）
        - content: 消息内容
        - atReceiver: 要@的人（可选）
        - replyMsgId: 这条是在回复哪条群消息（可选）；按群回复方式设置 @、拍一拍或引用对方，做不到时 @

    超过 2000 个字符时自动分批：在每批末尾 200 字符内寻找换行符切割，
    分批依次发送，仅第一批携带 @。
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    data = _payload(request)
    if data.get("is_master") and not robot.master:
        return ApiErrors.NO_MASTER.to_dict()
    receiver = _receiver(robot, data)
    content = data.get("content", "")
    at_receiver = data.get("atReceiver", "")
    reply_msg_id = data.get("replyMsgId", "")

    if not receiver or not content:
        return ApiErrors.INVALID_PARAMS.to_dict()

    success = await _run_wx_operation(_send_text_operation, robot, receiver, content, at_receiver, reply_msg_id)
    if success:
        return ApiResponse.success().to_dict()
    return ApiErrors.SEND_FAILED.to_dict()


@router.post("/send/file")
async def send_file(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    发送文件

    Request Body:
        - sendReceiver: 接收者
        - is_master: 为 true 时发给这个号的管理员，忽略 sendReceiver（可选）
        - url: 文件的下载地址，base 先下载到 send-files/ 再发送
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    data = _payload(request)
    if data.get("is_master") and not robot.master:
        return ApiErrors.NO_MASTER.to_dict()
    url = data.get("url", "")
    receiver = _receiver(robot, data)

    if not receiver or not url:
        return ApiErrors.INVALID_PARAMS.to_dict()

    try:
        path = await download(url, SEND_FILES_DIR)
    except Exception:
        LOG.warning("Failed to download file for [%s]", receiver, exc_info=True)
        return ApiErrors.SEND_FAILED.to_dict()

    success = await _run_wx_operation(robot.send_file_msg, path, receiver)
    if success:
        return ApiResponse.success().to_dict()
    return ApiErrors.SEND_FAILED.to_dict()


@router.post("/listen/add")
async def add_listen(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    添加聊天监听

    使用 Robot 的标准流程添加监听，会自动注入 on_message 回调。

    Request Body:
        - nickname: 聊天对象昵称

    Response:
        - data: {"success": bool}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    data = _payload(request)
    nickname = data.get("nickname", "")

    if not nickname:
        return ApiErrors.INVALID_PARAMS.to_dict()

    try:
        success = await _run_wx_operation(robot.add_listen_chat, nickname)
        return ApiResponse.success({"success": success}).to_dict()
    except Exception as e:
        LOG.exception("AddListenChat failed for [%s]", nickname)
        return ApiResponse.error(107, f"AddListenChat failed: {str(e)}").to_dict()


@router.post("/settings")
async def apply_settings(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    改设置，只改请求里带了的

    设置存在 server，后台改了以后通知 base 立即生效；base 下次连上微信时也会从 server 取。

    Request Body:
        - private_poll: 私聊轮询，没开子窗口的私聊靠主窗口红点来收（可选）
        - auto_accept_friends: 自动通过好友申请（可选）
        - group_reply: 群回复方式，at / tickle / quote 里选一个或多个，多个时随机挑（可选）

    Response:
        - data: 各项设置现在的值
    """
    robot = _get_robot()
    if robot is None:
        return ApiErrors.ROBOT_NOT_READY.to_dict()
    settings = _payload(request)
    styles = settings.get("group_reply", ["at"])
    switches_ok = all(isinstance(value, bool) for key, value in settings.items() if key != "group_reply")
    styles_ok = isinstance(styles, list) and styles and all(style in REPLY_STYLES for style in styles)
    if not settings or not switches_ok or not styles_ok:
        return ApiErrors.INVALID_PARAMS.to_dict()
    return ApiResponse.success(robot.apply_settings(settings)).to_dict()


@router.post("/groups/mute-all")
async def mute_all_groups() -> dict[str, Any]:
    """
    把会话列表里的群都设成消息免打扰，私聊轮询就不会点开它们；不活跃、不在会话列表里的群不管

    Response:
        - data: {"total", "muted": [...], "already": [...], "unread": [...], "failed": [{"chat", "reason"}]}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable
    try:
        return ApiResponse.success(await _run_wx_operation(robot.client.mute_all_groups)).to_dict()
    except Exception as e:
        LOG.exception("Failed to mute all groups")
        return ApiResponse.error(107, f"mute all groups failed: {e}").to_dict()


@router.post("/execute/wx")
async def execute_wx(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    通用执行 WeChat 实例方法

    动态调用 WeChat 类的方法，支持大部分 wxautox4 提供的功能。

    Request Body:
        - name: 方法名 (如 "SendMsg", "ChatWith", "GetMyInfo")
        - params: 参数字典 (如 {"msg": "hello", "who": "xxx"})，可选

    Response:
        - data: 方法执行结果

    Note:
        - 不允许调用 __ 或 _ 开头的方法
        - 不允许调用黑名单中的危险方法 (ShutDown, KeepRunning 等)
        - AddListenChat 请使用 /listen/add 独立接口
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    data = _payload(request)
    method_name = data.get("name", "")
    params = data.get("params", {})

    if not method_name:
        return ApiResponse.error(103, "Missing 'name' parameter").to_dict()

    if not is_method_allowed(method_name):
        return ApiResponse.error(106, f"Method '{method_name}' is not allowed").to_dict()

    return await _run_wx_operation(_execute_wx_operation, robot, method_name, params)


@router.post("/execute/chat")
async def execute_chat(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    通用执行 Chat 子窗口方法

    先通过 GetSubWindow 获取子窗口，再动态调用 Chat 类的方法。

    Request Body:
        - chat_name: 聊天对象名称 (用于 GetSubWindow 获取子窗口)
        - name: 方法名 (如 "SendMsg", "ChatInfo", "GetAllMessage")
        - params: 参数字典，可选

    Response:
        - data: 方法执行结果

    Note:
        - 子窗口必须已经存在（通过 AddListenChat 创建）
        - 不允许调用 __ 或 _ 开头的方法
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    data = _payload(request)
    chat_name = data.get("chat_name", "")
    method_name = data.get("name", "")
    params = data.get("params", {})

    if not chat_name:
        return ApiResponse.error(103, "Missing 'chat_name' parameter").to_dict()
    if not method_name:
        return ApiResponse.error(103, "Missing 'name' parameter").to_dict()

    if not is_method_allowed(method_name):
        return ApiResponse.error(106, f"Method '{method_name}' is not allowed").to_dict()

    return await _run_wx_operation(_execute_chat_operation, robot, chat_name, method_name, params)


@router.post("/listen/status")
async def listen_status(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    查监听是否健康：注册时弹出的聊天窗口还在、标题也还是这个聊天对象，只读窗口句柄，很快

    Request Body:
        - chat_names: 聊天对象名称列表

    Response:
        - data: {"results": {"chat_name1": null, "chat_name2": "window_not_found", ...}}
          值是不健康的原因，健康时为 null；not_listening 表示 base 这次连上微信后没注册过它
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    chat_names = _payload(request).get("chat_names", [])
    if not chat_names or not isinstance(chat_names, list):
        return ApiResponse.error(103, "Missing or invalid 'chat_names' parameter").to_dict()

    return ApiResponse.success({"results": robot.client.listen_health(chat_names)}).to_dict()


@router.post("/listen/probe")
async def listen_probe(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    测活：读一遍聊天窗口里的消息

    Request Body:
        - chat_name: 聊天对象名称

    Response:
        - data: {"count": 消息条数, "last": {"sender", "type", "content"} 或 null}
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    chat_name = _payload(request).get("chat_name", "")
    if not chat_name:
        return ApiErrors.INVALID_PARAMS.to_dict()

    try:
        result = await _run_wx_operation(robot.client.probe_listen, chat_name)
    except Exception as e:
        LOG.exception("Probe failed for [%s]", chat_name)
        return ApiResponse.error(107, f"Probe failed: {str(e)}").to_dict()
    if result is None:
        return ApiResponse.error(108, f"Sub window '{chat_name}' not found. Please add listener first.").to_dict()
    return ApiResponse.success(result).to_dict()


def _get_robot() -> Optional["Robot"]:
    from true_love_base.api.server import get_robot

    return get_robot()


def _unavailable(robot: Optional["Robot"]) -> Optional[dict[str, Any]]:
    """微信用不了时返回对应的错误响应，可用时返回 None"""
    if robot is None:
        return ApiErrors.ROBOT_NOT_READY.to_dict()
    if not robot.client.is_connected():
        return ApiErrors.WECHAT_OFFLINE.to_dict()
    return None


def _receiver(robot: "Robot", data: dict[str, Any]) -> str:
    """消息发给谁：指明发给管理员时用这个号的管理员，否则用请求里的接收者"""
    return robot.master if data.get("is_master") else data.get("sendReceiver", "")


def _payload(data: dict[str, Any] | None) -> dict[str, Any]:
    return data or {}


async def _run_wx_operation(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await run_in_threadpool(func, *args, **kwargs)


def is_method_allowed(method_name: str) -> bool:
    """
    检查方法是否允许调用

    Args:
        method_name: 方法名

    Returns:
        是否允许调用
    """
    if method_name.startswith("__"):
        return False
    if method_name.startswith("_"):
        return False
    if method_name in METHOD_BLACKLIST:
        return False
    return True


def split_long_text(text: str) -> list[str]:
    """
    将文本按 UTF-8 字节长度进行拆分，每批不超过 MAX_CHUNK_BYTES。
    尽量在换行符处拆分以保持美观。
    """
    chunks = []
    if not text:
        return chunks

    remaining = text
    while remaining:
        if len(remaining.encode("utf-8")) <= MAX_CHUNK_BYTES:
            chunks.append(remaining)
            break

        low = 0
        high = len(remaining)
        split_point = 0
        while low <= high:
            mid = (low + high) // 2
            if len(remaining[:mid].encode("utf-8")) <= MAX_CHUNK_BYTES:
                split_point = mid
                low = mid + 1
            else:
                high = mid - 1

        chunk_text = remaining[:split_point]
        last_newline = chunk_text.rfind("\n")
        if last_newline != -1 and last_newline > split_point * 0.6:
            actual_split = last_newline + 1
        else:
            actual_split = split_point

        chunks.append(remaining[:actual_split])
        remaining = remaining[actual_split:]

    return chunks


def serialize_result(result: Any) -> Any:
    """
    通用序列化返回值

    Args:
        result: 方法返回值

    Returns:
        可 JSON 序列化的结果
    """
    if result is None:
        return None

    if isinstance(result, (str, int, float, bool)):
        return result

    if isinstance(result, dict):
        return result

    if isinstance(result, list):
        return [serialize_result(item) for item in result]

    if hasattr(result, "get") and hasattr(result, "__getitem__"):
        try:
            return dict(result)
        except (TypeError, ValueError):
            pass

    try:
        attrs = {}
        for attr in dir(result):
            if attr.startswith("_"):
                continue
            try:
                value = getattr(result, attr)
                if not callable(value):
                    json.dumps(value, ensure_ascii=False)
                    attrs[attr] = value
            except (TypeError, ValueError, AttributeError):
                pass
        if attrs:
            return attrs
    except Exception:
        pass

    return str(result)


def _send_text_operation(robot: "Robot", receiver: str, content: str, at_receiver: str,
                         reply_msg_id: str = "") -> bool:
    if len(content.encode("utf-8")) <= MAX_CHUNK_BYTES:
        return robot.send_text_msg(content, receiver, at_receiver if at_receiver else None, reply_msg_id)

    chunks = split_long_text(content)
    LOG.info(
        "send_text: content too long (%d bytes), splitting into %d chunks",
        len(content.encode("utf-8")),
        len(chunks),
    )
    for idx, chunk in enumerate(chunks):
        mention = at_receiver if idx == 0 and at_receiver else None
        ok = robot.send_text_msg(chunk, receiver, mention, reply_msg_id if idx == 0 else "")
        if not ok:
            LOG.error("send_text: failed on chunk %d/%d to [%s]", idx + 1, len(chunks), receiver)
            return False
    return True


def _execute_wx_operation(robot: "Robot", method_name: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        wx = robot.client.wx
    except Exception as e:
        # 微信刚掉线时会这样，原因看异常就够了
        LOG.warning("Failed to get wx instance: %s", e)
        return ApiResponse.error(101, "WeChat client not ready").to_dict()

    method = getattr(wx, method_name, None)
    if method is None or not callable(method):
        return ApiResponse.error(106, f"Method '{method_name}' not found").to_dict()

    try:
        LOG.info("Executing wx.%s(%s)", method_name, params)
        result = method(**params) if params else method()
        serialized = serialize_result(result)
        LOG.info("wx.%s result: %s", method_name, str(serialized))
        return ApiResponse.success(serialized).to_dict()
    except TypeError as e:
        # 多半是调用方传错了参数，原因看异常就够了
        LOG.warning("wx.%s TypeError: %s", method_name, e)
        return ApiResponse.error(103, f"Invalid params: {str(e)}").to_dict()
    except Exception as e:
        LOG.exception("wx.%s execution failed", method_name)
        return ApiResponse.error(107, f"Execution failed: {str(e)}").to_dict()


def _execute_chat_operation(
    robot: "Robot",
    chat_name: str,
    method_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        wx = robot.client.wx
    except Exception as e:
        # 微信刚掉线时会这样，原因看异常就够了
        LOG.warning("Failed to get wx instance: %s", e)
        return ApiResponse.error(101, "WeChat client not ready").to_dict()

    try:
        chat = wx.GetSubWindow(chat_name)
        if chat is None:
            return ApiResponse.error(108, f"Sub window '{chat_name}' not found. Please add listener first.").to_dict()
    except Exception as e:
        LOG.exception("GetSubWindow failed for [%s]", chat_name)
        return ApiResponse.error(108, f"Failed to get sub window: {str(e)}").to_dict()

    method = getattr(chat, method_name, None)
    if method is None or not callable(method):
        return ApiResponse.error(106, f"Method '{method_name}' not found on Chat").to_dict()

    try:
        LOG.info("Executing chat[%s].%s(%s)", chat_name, method_name, params)
        result = method(**params) if params else method()
        serialized = serialize_result(result)
        LOG.info("chat.%s result: %s", method_name, str(serialized))
        return ApiResponse.success(serialized).to_dict()
    except TypeError as e:
        LOG.warning("chat.%s TypeError: %s", method_name, e)
        return ApiResponse.error(103, f"Invalid params: {str(e)}").to_dict()
    except Exception as e:
        LOG.exception("chat.%s execution failed", method_name)
        return ApiResponse.error(107, f"Execution failed: {str(e)}").to_dict()

