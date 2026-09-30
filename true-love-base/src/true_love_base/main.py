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

from true_love_common.wechat_account import current_wxid

from true_love_base.api import server
from true_love_base.configuration import Config
from true_love_base.core import WxAutoClient
from true_love_base.services import server_client
from true_love_base.services.robot import Robot
from true_love_base.services.wx_supervisor import WxSupervisor
from true_love_base.utils.tailnet import tailnet_ip
from true_love_base.utils.win_env import display_scale_percent, keep_awake

# 初始化配置（会设置日志）
config = Config()
LOG = logging.getLogger("Main")


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
        kernel32 = ctypes.windll.kernel32
        # 获取标准输入句柄 (STD_INPUT_HANDLE = -10)
        handle = kernel32.GetStdHandle(-10)
        # 获取当前控制台模式
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        # 禁用 QuickEdit 模式 (0x0040) 和插入模式 (0x0020)
        # ENABLE_QUICK_EDIT_MODE = 0x0040
        # ENABLE_INSERT_MODE = 0x0020
        new_mode = mode.value & ~0x0040 & ~0x0020
        kernel32.SetConsoleMode(handle, new_mode)
        LOG.info("Disabled Windows console QuickEdit mode")
    except Exception as e:
        # 非 Windows 环境或没有控制台时忽略
        LOG.debug(f"Could not disable QuickEdit mode: {e}")


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
    LOG.info("Bot [%s] reports callback %s to the server", config.bot_id, config.callback)

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

    if not robot.master:
        LOG.warning(f"Bot [{config.bot_id}] has no master, startup and shutdown are not announced")

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
                if not robot.send_text_msg("True Love Base shutting down...", robot.master):
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

    if unavailable:
        LOG.error("Listen chats unavailable from server; not listening to any chat until the server restores them")
    elif len(success_chats) > 0:
        LOG.info(f"Loaded {len(success_chats)} listen chats from server")
    else:
        LOG.warning("No listen chats on server! Use API to add listeners")

    if len(failed_chats) > 0:
        LOG.warning(f"Failed to load {len(failed_chats)} listen chats: {failed_chats}")

    if not robot.master:
        return True

    # 发送启动通知，包含监听成功和失败的列表
    try:
        success_list_str = "\n".join(
            [f"  {i + 1}. {name}" for i, name in enumerate(success_chats)]) if success_chats else "  (无)"
        failed_list_str = "\n".join(
            [f"  {i + 1}. {name}" for i, name in enumerate(failed_chats)]) if failed_chats else "  (无)"

        headline = "WeChat reconnected!" if reconnected else "True Love Base started successfully!"
        startup_msg = f"{headline}\n\n当前监听列表 ({len(success_chats)}个):\n{success_list_str}"
        if failed_chats:
            startup_msg += f"\n\n监听失败 ({len(failed_chats)}个):\n{failed_list_str}"
        if unavailable:
            startup_msg += "\n\n没从 server 取到监听列表，暂时不监听任何聊天；server 启动后会自动补上"

        scale = display_scale_percent()
        if scale is not None and scale != 100:
            LOG.warning(f"Display scaling is {scale}%, wxautox4 needs 100%")
            startup_msg += f"\n\n屏幕缩放是 {scale}%，请调成 100%，否则下载图片等操作可能失败"

        if not robot.send_text_msg(startup_msg, robot.master):
            LOG.warning("Startup notification was not delivered to [%s]", robot.master)
    except Exception:
        LOG.warning("Failed to send startup notification to [%s]", robot.master, exc_info=True)
    return True


def init_wx() -> tuple[WxAutoClient, Robot]:
    # 初始化微信客户端；连接微信由 WxSupervisor 负责，微信没开也不影响 base 启动
    # 连接时核对登录的号，不是部署时指定的那个就不接管
    client = WxAutoClient(bot_id=config.bot_id, account_of=current_wxid)
    server_client.use_self_name(client.get_self_name)

    # 初始化机器人；监听列表在连上微信后向 server 取
    robot = Robot(client, master=config.master_wix)
    return client, robot
