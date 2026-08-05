# -*- coding: UTF-8 -*-
"""桌面发行版入口：保持本机端口，并在设置保存后托管服务重启。"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from backend.runtime import RESTART_EXIT_CODE, is_frozen


_server_log_stream = None


def _server_log_path() -> Path:
    configured_data_dir = os.getenv("APP_DATA_DIR", "").strip()
    data_dir = Path(configured_data_dir).expanduser() if configured_data_dir else (
        Path(os.getenv("LOCALAPPDATA") or Path.home()) / "VirtualCompanion"
    )
    log_directory = data_dir / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    return log_directory / "server.log"


def _clear_server_log() -> None:
    """在新服务进程启动前截断上一轮日志。"""
    _server_log_path().write_text("", encoding="utf-8")


def _redirect_server_output() -> None:
    """将冻结版服务子进程的全部控制台输出写入用户日志目录。"""
    global _server_log_stream
    if not is_frozen():
        return

    _server_log_stream = _server_log_path().open(
        "a",
        encoding="utf-8",
        buffering=1,
        errors="backslashreplace",
    )
    sys.stdout = _server_log_stream
    sys.stderr = _server_log_stream
    print(f"\n===== server started {datetime.now().isoformat(timespec='seconds')} =====")


def _open_browser_when_ready(port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                webbrowser.open(f"http://127.0.0.1:{port}/app/")
                return
        except OSError:
            time.sleep(0.2)


def main() -> int:
    # CEF 使用当前可执行文件启动 renderer/gpu 等子进程；这些子进程不能进入服务启动流程。
    if any(argument.startswith("--type=") for argument in sys.argv[1:]):
        return 0

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--port", type=int)
    args, _ = parser.parse_known_args()

    if args.server:
        if os.getenv("VIRTUAL_COMPANION_LOG_PREPARED") != "1":
            _clear_server_log()
        _redirect_server_output()

    from backend.main import _resolve_server_port, run_server

    if args.server:
        run_server(args.port or _resolve_server_port())
        return 0

    port = _resolve_server_port()
    threading.Thread(target=_open_browser_when_ready, args=(port,), daemon=True).start()

    if not is_frozen():
        run_server(port)
        return 0

    command = [sys.executable, "--server", "--port", str(port)]
    while True:
        _clear_server_log()
        child_environment = os.environ.copy()
        child_environment["VIRTUAL_COMPANION_LOG_PREPARED"] = "1"
        exit_code = subprocess.call(command, env=child_environment)
        if exit_code != RESTART_EXIT_CODE:
            return exit_code
        time.sleep(0.25)


if __name__ == "__main__":
    raise SystemExit(main())
