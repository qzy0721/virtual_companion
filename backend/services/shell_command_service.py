"""桌面外壳命令总线：CEF/网页请求，C# 主控长轮询消费。"""

import asyncio


class ShellCommandService:
    _ALLOWED_COMMANDS = {"show-main"}

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)

    async def publish(self, command: str) -> None:
        if command not in self._ALLOWED_COMMANDS:
            raise ValueError(f"不支持的桌面外壳命令: {command}")
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(command)

    async def next_command(self, timeout_seconds: float = 20.0) -> str | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return None


_shell_command_service = ShellCommandService()


def get_shell_command_service() -> ShellCommandService:
    return _shell_command_service
