"""C# 主控命令队列离线测试。"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# 直接运行 `python tests/test_shell_commands.py` 时，sys.path[0] 是 tests 目录，
# 需要在导入 backend 之前把其父目录（项目根目录）加入模块搜索路径。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.main import app
from backend.services.shell_command_service import ShellCommandService


class ShellCommandContracts(unittest.IsolatedAsyncioTestCase):
    async def test_show_main_command_round_trip(self):
        service = ShellCommandService()
        await service.publish("show-main")
        self.assertEqual(await service.next_command(0.1), "show-main")

    async def test_long_poll_timeout_returns_none(self):
        service = ShellCommandService()
        self.assertIsNone(await service.next_command(0.01))

    async def test_unknown_command_is_rejected(self):
        service = ShellCommandService()
        with self.assertRaises(ValueError):
            await service.publish("arbitrary-command")


class ShellCommandApiContracts(unittest.TestCase):
    def test_control_token_protects_long_poll_consumer(self):
        with patch.dict(os.environ, {"SHELL_CONTROL_TOKEN": "test-shell-token"}):
            client = TestClient(app)
            queued = client.post("/api/shell/show-main")
            denied = client.get(
                "/api/shell/next?timeout_seconds=1",
                headers={"X-Shell-Control-Token": "wrong"},
            )
            consumed = client.get(
                "/api/shell/next?timeout_seconds=1",
                headers={"X-Shell-Control-Token": "test-shell-token"},
            )

        self.assertEqual(queued.status_code, 202)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(consumed.json(), {"command": "show-main"})


if __name__ == "__main__":
    unittest.main()
