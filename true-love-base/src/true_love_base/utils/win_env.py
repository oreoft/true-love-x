# -*- coding: utf-8 -*-
"""
WinEnv - Windows 运行环境

wxautox4 靠 UI 自动化操作微信，对桌面环境有要求：
- 机器不能睡眠、熄屏，否则微信窗口操作不了
- 屏幕缩放必须是 100%，否则下载图片等依赖坐标的操作会失败
"""

import logging
import sys
from typing import Optional

LOG = logging.getLogger("WinEnv")

# SetThreadExecutionState 的标志位
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# 100% 缩放对应的 DPI
STANDARD_DPI = 96

# 读显示器 DPI 用到的常量
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
MONITOR_DEFAULTTOPRIMARY = 1
MDT_EFFECTIVE_DPI = 0


def keep_awake() -> None:
    """
    阻止系统睡眠和熄屏

    状态绑定在调用线程上，线程结束后自动恢复，所以要在主线程里调用。
    """
    if sys.platform != 'win32':
        return

    try:
        import ctypes
        previous = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )
    except Exception as e:
        LOG.warning(f"Could not keep the system awake: {e}")
        return
    # 失败时返回 NULL
    if not previous:
        LOG.warning("Windows rejected the request to keep the system awake")
        return
    LOG.info("Keeping the system and display awake")


def _monitor_dpi() -> Optional[int]:
    """
    主显示器当前的 DPI（改缩放后立即生效）

    只在当前线程里临时声明 DPI 感知，读完就恢复，不改变整个进程（也就是 wxautox4）的坐标系。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    if not previous:
        return None
    try:
        monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), MDT_EFFECTIVE_DPI,
                                                 ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:
            return None
        return dpi_x.value
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def _applied_dpi() -> int:
    """注册表里的 AppliedDPI：登录时写入，改了缩放要注销重新登录才会更新"""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics") as key:
        dpi, _ = winreg.QueryValueEx(key, "AppliedDPI")
    return dpi


def display_scale_percent() -> Optional[int]:
    """
    获取主显示器当前的屏幕缩放比例

    先问系统主显示器的实际 DPI；读不到时退回注册表里登录时的值（改缩放后要注销才会更新）。

    Returns:
        缩放百分比（100 表示 100%），读不到时返回 None
    """
    if sys.platform != 'win32':
        return None

    for read in (_monitor_dpi, _applied_dpi):
        try:
            dpi = read()
        except Exception as e:
            LOG.debug(f"Could not read display scaling: {e}")
            continue
        if dpi:
            return round(dpi * 100 / STANDARD_DPI)
    return None
