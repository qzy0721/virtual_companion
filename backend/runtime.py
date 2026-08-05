# -*- coding: UTF-8 -*-
"""运行环境辅助函数：区分源码/包内资源与用户可写数据。"""

from __future__ import annotations

import os
import sys
import threading
import time


RESTART_EXIT_CODE = 75


def is_frozen() -> bool:
    """当前进程是否由 PyInstaller 启动。"""
    return bool(getattr(sys, "frozen", False))


def schedule_frozen_restart(delay_seconds: float = 0.75) -> bool:
    """让冻结态服务进程退出，由父启动器以相同端口拉起新实例。"""
    if not is_frozen():
        return False

    def exit_process() -> None:
        time.sleep(delay_seconds)
        os._exit(RESTART_EXIT_CODE)

    threading.Thread(target=exit_process, name="settings-restart", daemon=True).start()
    return True
