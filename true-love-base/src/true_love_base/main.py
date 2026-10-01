# -*- coding: utf-8 -*-
"""
True Love Base - Main Entry Point

微信消息处理服务的主入口。
基于 wxautox4 实现微信自动化。
"""

import logging
import signal
import sys
from threading import Event

from true_love_common.observability.logging import LoggingConfig
from true_love_common.wechat_account import current_wxid

from true_love_base.api import server
from true_love_base.configuration import Config
from true_love_base.core import WxAutoClient
from true_love_base.services import server_client
from true_love_base.services.robot import Robot
from true_love_base.services.wx_supervisor import WxSupervisor
from true_love_base.utils.tailnet import tailnet_ip
from true_love_base.utils.win_env import keep_awake

# 初始化配置（会设置日志）
config = Config()
LOG = logging.getLogger("Main")
REPLY_STYLE_NAMES = {"at": "@", "tickle": "拍一拍", "quote": "引用"}


def disable_quick_edit():
    """
    禁用 Windows 控制台的 QuickEdit 模式
    
    QuickEdit 模式会导致用户点击控制台窗口时程序暂停，
    直到按回车键才会继续执行，这会影响消息监听的稳定性。
    """
    if sys.platform != 'win32':
        return

    try:
        import ctypes
        # use_last_error 让 ctypes 在调用后立刻保存 GetLastError()，避免被中间的调用覆盖
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 获取标准输入句柄 (STD_INPUT_HANDLE = -10)
        handle = kernel32.GetStdHandle(-10)
        # 获取当前控制台模式；失败时返回 0
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            LOG.warning("Could not disable QuickEdit mode: GetConsoleMode failed, error %s",
                        ctypes.get_last_error())
            return
        # 禁用 QuickEdit 模式 (0x0040) 和插入模式 (0x0020)
        # ENABLE_QUICK_EDIT_MODE = 0x0040
        # ENABLE_INSERT_MODE = 0x0020
        # 不带 ENABLE_EXTENDED_FLAGS (0x0080) 时 Windows 不认 QuickEdit 位的改动
        new_mode = (mode.value | 0x0080) & ~0x0040 & ~0x0020
        if not kernel32.SetConsoleMode(handle, new_mode):
            LOG.warning("Could not disable QuickEdit mode: SetConsoleMode failed, error %s",
                        ctypes.get_last_error())
            return
        LOG.info("Disabled Windows console QuickEdit mode")
    except Exception:
        LOG.warning("Could not disable QuickEdit mode", exc_info=True)


def main():
    """主函数"""
    # 禁用 Windows 控制台 QuickEdit 模式，防止点击窗口导致程序暂停
    disable_quick_edit()
    # 机器睡眠或熄屏后微信窗口操作不了；状态绑定在主线程上，base 退出后自动恢复
    keep_awake()
    LOG.info("=" * 50)
    LOG.info("True Love Base starting...")
    LOG.info("=" * 50)

    # server 只通过 tailnet 回调 base，连不上 tailnet 就不启动
    try:
        config.callback = f"http://{tailnet_ip()}:{server.HTTP_PORT}"
    except RuntimeError as e:
        raise SystemExit(f"Cannot start without a tailnet address: {e}")
    LOG.info("Reporting callback %s to the server", config.callback)

    # 初始化微信客户端和机器人（不连接微信）
    client, robot = init_wx()

    # 关闭事件，用于优雅退出
    shutdown_event = Event()

    # 设置信号处理
    def signal_handler(sig, frame):
        LOG.info("Received shutdown signal %s, shutting down...", sig)
        shutdown_event.set()  # 通知主线程退出

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 是否已经向 master 报告过启动成功
    announced = False

    def start_listening() -> None:
        nonlocal announced
        if init_listening(robot, shutdown_event, reconnected=announced):
            announced = True

    try:
        # HTTP 先起来：微信没开时接口照常响应，只是返回微信离线
        server.enable_http(robot)
        LOG.info("HTTP server enabled")
        LOG.info("True Love Base is ready! Waiting for WeChat...")

        # 在主线程里守护微信连接，直到收到退出信号；AddListenChat starts SDK listening.
        WxSupervisor(client, start_listening, shutdown_event).run()

        if announced and robot.master and client.is_connected():
            try:
                if not robot.send_text_msg("tl-base 正在关闭...", robot.master):
                    LOG.warning("Shutdown notification was not delivered to [%s]", robot.master)
            except Exception:
                LOG.warning("Failed to send shutdown notification to [%s]", robot.master, exc_info=True)
    except Exception:
        LOG.exception("Base runtime failed; shutting down")
        raise
    finally:
        LOG.info("Cleaning up...")
        try:
            client.cleanup()
        finally:
            robot.cleanup()
            server_client.stop_retrying()
        LOG.info("Cleanup completed, bye!")


def init_listening(robot: Robot, stop_event: Event, *, reconnected: bool = False) -> bool:
    """
    Register saved listeners after WeChat connects; the SDK owns its listener threads.

    Returns:
        False when shutdown cancelled the registration, so nothing was announced
    """
    load_result = robot.load_listen_chats(stop_event=stop_event)
    if stop_event.is_set():
        return False
    success_chats = load_result["success"]
    failed_chats = load_result["failed"]
    unavailable = load_result["unavailable"]
    settings = load_result.get("settings", {})

    if unavailable:
        LOG.error("Listen chats unavailable from server; not listening to any chat until the server restores them")
    elif len(success_chats) > 0:
        LOG.info("Loaded %s listen chats from server", len(success_chats))
    else:
        LOG.warning("No listen chats on server! Use API to add listeners")

    if len(failed_chats) > 0:
        LOG.warning("Failed to load %s listen chats: %s", len(failed_chats), failed_chats)

    if not robot.master:
        LOG.warning("Bot [%s] has no master, startup and shutdown are not announced", robot.client.bot_id)
        return True

    # 发送启动通知，包含监听成功和失败的列表
    try:
        success_list_str = "\n".join(
            [f"  {i + 1}. {name}" for i, name in enumerate(success_chats)]) if success_chats else "  (无)"
        failed_list_str = "\n".join(
            [f"  {i + 1}. {name}" for i, name in enumerate(failed_chats)]) if failed_chats else "  (无)"

        headline = "tl-base 重新连上微信" if reconnected else "tl-base 启动成功"
        startup_msg = f"{headline}\n\n当前监听列表 ({len(success_chats)}个):\n{success_list_str}"
        styles = "、".join(REPLY_STYLE_NAMES.get(style, style) for style in settings.get("group_reply", []))
        startup_msg += (f"\n\n私聊轮询：{'开' if settings.get('private_poll') else '关'}"
                        f"\n自动通过好友申请：{'开' if settings.get('auto_accept_friends') else '关'}"
                        f"\n群回复方式：{styles or '@'}")
        if failed_chats:
            startup_msg += f"\n\n监听失败 ({len(failed_chats)}个):\n{failed_list_str}"
        if unavailable:
            startup_msg += "\n\n没从 server 取到监听列表，暂时不监听任何聊天；server 启动后会自动补上"

        if not robot.send_text_msg(startup_msg, robot.master):
            LOG.warning("Startup notification was not delivered to [%s]", robot.master)
    except Exception:
        LOG.warning("Failed to send startup notification to [%s]", robot.master, exc_info=True)
    return True


def init_wx() -> tuple[WxAutoClient, Robot]:
    # 初始化微信客户端；连接微信由 WxSupervisor 负责，微信没开也不影响 base 启动
    # 这个 base 跑的是哪个号，每次连上微信时从本机读当前登录的 wxid
    client = WxAutoClient(account_of=current_wxid)
    server_client.use_identity(lambda: client.bot_id, client.get_self_name)
    LoggingConfig.add_loki_tags(lambda: {"bot_id": client.bot_id})

    # 初始化机器人；监听列表在连上微信后向 server 取
    robot = Robot(client, master_of=config.master_of)
    server_client.on_server_down(robot.announce_server_down)
    return client, robot
