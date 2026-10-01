# -*- coding: utf-8 -*-
"""HTTP routes for true-love-base."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

from fastapi import APIRouter, Body
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

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
        - quoteMsgId: 要引用回复的消息 id（可选）；原消息还在监听窗口里时引用回复，否则照常发送

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
    quote_msg_id = data.get("quoteMsgId", "")

    if not receiver or not content:
        return ApiErrors.INVALID_PARAMS.to_dict()

    success = await _run_wx_operation(_send_text_operation, robot, receiver, content, at_receiver, quote_msg_id)
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
    except Exception as e:
        LOG.error("Failed to download file for [%s]: %s", receiver, e)
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
        LOG.error("AddListenChat failed for [%s]: %s", nickname, e)
        return ApiResponse.error(107, f"AddListenChat failed: {str(e)}").to_dict()


@router.post("/settings")
async def apply_settings(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    打开或关闭功能开关，只改请求里带了的

    开关存在 server，后台改了以后通知 base 立即生效；base 下次连上微信时也会从 server 取。

    Request Body:
        - private_poll: 私聊轮询，没开子窗口的私聊靠主窗口红点来收（可选）
        - auto_accept_friends: 自动通过好友申请（可选）

    Response:
        - data: 各项开关现在的状态
    """
    robot = _get_robot()
    if robot is None:
        return ApiErrors.ROBOT_NOT_READY.to_dict()
    switches = _payload(request)
    if not switches or not all(isinstance(value, bool) for value in switches.values()):
        return ApiErrors.INVALID_PARAMS.to_dict()
    return ApiResponse.success(robot.apply_switches(switches)).to_dict()


@router.post("/groups/mute-all")
async def mute_all_groups() -> dict[str, Any]:
    """
    把通讯录里的群都设成消息免打扰，私聊轮询就不会点开它们；逐个点右键菜单，群多时要等一会儿

    Response:
        - data: {"total", "muted": [...], "already": [...], "failed": [{"chat", "reason"}]}
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


@router.post("/execute/batch-chat-info")
async def batch_chat_info(request: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """
    批量获取 Chat 的 ChatInfo

    一次性获取多个聊天窗口的状态信息，避免多次请求。

    Request Body:
        - chat_names: 聊天对象名称列表

    Response:
        - data: {
            "results": {
                "chat_name1": {"success": true, "data": {...}},
                "chat_name2": {"success": false, "reason": "window_not_found"},
                ...
            }
        }
    """
    robot = _get_robot()
    unavailable = _unavailable(robot)
    if unavailable is not None:
        return unavailable

    data = _payload(request)
    chat_names = data.get("chat_names", [])

    if not chat_names or not isinstance(chat_names, list):
        return ApiResponse.error(103, "Missing or invalid 'chat_names' parameter").to_dict()

    return await _run_wx_operation(_batch_chat_info_operation, robot, chat_names)


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
                         quote_msg_id: str = "") -> bool:
    if len(content.encode("utf-8")) <= MAX_CHUNK_BYTES:
        return robot.send_text_msg(content, receiver, at_receiver if at_receiver else None, quote_msg_id)

    chunks = split_long_text(content)
    LOG.info(
        "send_text: content too long (%d bytes), splitting into %d chunks",
        len(content.encode("utf-8")),
        len(chunks),
    )
    for idx, chunk in enumerate(chunks):
        mention = at_receiver if idx == 0 and at_receiver else None
        ok = robot.send_text_msg(chunk, receiver, mention, quote_msg_id if idx == 0 else "")
        if not ok:
            LOG.error("send_text: failed on chunk %d/%d to [%s]", idx + 1, len(chunks), receiver)
            return False
    return True


def _execute_wx_operation(robot: "Robot", method_name: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        wx = robot.client.wx
    except Exception as e:
        LOG.error("Failed to get wx instance: %s", e)
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
        LOG.error("wx.%s TypeError: %s", method_name, e)
        return ApiResponse.error(103, f"Invalid params: {str(e)}").to_dict()
    except Exception as e:
        LOG.error("wx.%s execution failed: %s", method_name, e)
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
        LOG.error("Failed to get wx instance: %s", e)
        return ApiResponse.error(101, "WeChat client not ready").to_dict()

    try:
        chat = wx.GetSubWindow(chat_name)
        if chat is None:
            return ApiResponse.error(108, f"Sub window '{chat_name}' not found. Please add listener first.").to_dict()
    except Exception as e:
        LOG.error("GetSubWindow failed for [%s]: %s", chat_name, e)
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
        LOG.error("chat.%s TypeError: %s", method_name, e)
        return ApiResponse.error(103, f"Invalid params: {str(e)}").to_dict()
    except Exception as e:
        LOG.error("chat.%s execution failed: %s", method_name, e)
        return ApiResponse.error(107, f"Execution failed: {str(e)}").to_dict()


def _batch_chat_info_operation(robot: "Robot", chat_names: list[str]) -> dict[str, Any]:
    try:
        wx = robot.client.wx
    except Exception as e:
        LOG.error("Failed to get wx instance: %s", e)
        return ApiResponse.error(101, "WeChat client not ready").to_dict()

    results = {}
    for chat_name in chat_names:
        try:
            chat = wx.GetSubWindow(chat_name)
            if chat is None:
                results[chat_name] = {"success": False, "reason": "window_not_found"}
                continue

            chat_info = chat.ChatInfo()
            serialized = serialize_result(chat_info)
            results[chat_name] = {"success": True, "data": serialized}
        except Exception as e:
            LOG.error("batch_chat_info failed for [%s]: %s", chat_name, e)
            results[chat_name] = {"success": False, "reason": str(e)}

    LOG.info("batch_chat_info completed for %d chats", len(chat_names))
    return ApiResponse.success({"results": results}).to_dict()
