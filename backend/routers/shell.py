"""CEF/网页与 C# WinForms 主控之间的本地控制 API。"""

import os
import secrets

from fastapi import APIRouter, Header, HTTPException, Request, Response

from backend.services.shell_command_service import get_shell_command_service


router = APIRouter(prefix="/api/shell", tags=["shell"])


def _require_local_client(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="桌面外壳命令仅接受本机请求")


@router.post("/show-main", status_code=202)
async def show_main_window(request: Request):
    """供 CEF 桌宠请求恢复并激活 WinForms 主窗口。"""
    _require_local_client(request)
    await get_shell_command_service().publish("show-main")
    return {"status": "queued", "command": "show-main"}


@router.get("/next")
async def next_shell_command(
    request: Request,
    timeout_seconds: float = 20.0,
    x_shell_control_token: str = Header(default=""),
):
    """C# 主控长轮询命令；控制令牌只通过父进程环境传递，不暴露给网页。"""
    _require_local_client(request)
    expected_token = os.getenv("SHELL_CONTROL_TOKEN", "")
    if not expected_token or not secrets.compare_digest(x_shell_control_token, expected_token):
        raise HTTPException(status_code=403, detail="桌面外壳控制令牌无效")

    timeout = max(1.0, min(float(timeout_seconds), 25.0))
    command = await get_shell_command_service().next_command(timeout)
    if command is None:
        return Response(status_code=204)
    return {"command": command}
