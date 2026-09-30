# -*- coding: utf-8 -*-
"""
WinEnv - Windows 运行环境

wxautox4 靠 UI 自动化操作微信，机器不能睡眠、熄屏，否则微信窗口操作不了。
"""

import logging
import sys

LOG = logging.getLogger("WinEnv")

# SetThreadExecutionState 的标志位
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


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
