# -*- coding: UTF-8 -*-
"""DashScope Paraformer 实时语音识别 WebSocket 会话。"""

import inspect
import json
import uuid
from typing import Any, Optional

from config import (
    ASR_REALTIME_MODEL,
    ASR_REALTIME_SAMPLE_RATE,
    ASR_VAD_SILENCE_MS,
    DASHSCOPE_API_KEY,
    DASHSCOPE_ASR_WS_URL,
    DASHSCOPE_WORKSPACE_ID,
)


class ASRStreamError(RuntimeError):
    """实时 ASR 会话无法继续时抛出。"""


def _get_websocket_url() -> str:
    if DASHSCOPE_ASR_WS_URL:
        return DASHSCOPE_ASR_WS_URL
    if DASHSCOPE_WORKSPACE_ID:
        return (
            f"wss://{DASHSCOPE_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/"
            "api-ws/v1/inference"
        )
    return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


class DashScopeRealtimeASRSession:
    """一个浏览器连接对应一个 DashScope WebSocket 连接。"""

    def __init__(self) -> None:
        self._connection: Optional[Any] = None
        self.task_id: Optional[str] = None
        self.finishing = False

    @property
    def is_task_active(self) -> bool:
        return self.task_id is not None

    async def connect(self) -> None:
        if self._connection is not None:
            return
        if not DASHSCOPE_API_KEY:
            raise ASRStreamError("服务端未配置 DASHSCOPE_API_KEY")

        try:
            import websockets
        except ImportError as exc:
            raise ASRStreamError(
                "缺少 websockets 依赖，请安装：pip install websockets"
            ) from exc

        connect = websockets.connect
        headers = {
            "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
            "User-Agent": "virtual-companion-asr/2.1",
        }
        if DASHSCOPE_WORKSPACE_ID:
            headers["X-DashScope-WorkSpace"] = DASHSCOPE_WORKSPACE_ID

        kwargs: dict[str, Any] = {
            "open_timeout": 10,
            "close_timeout": 5,
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_size": 2 * 1024 * 1024,
        }
        header_argument = (
            "additional_headers"
            if "additional_headers" in inspect.signature(connect).parameters
            else "extra_headers"
        )
        kwargs[header_argument] = headers

        try:
            self._connection = await connect(_get_websocket_url(), **kwargs)
        except Exception as exc:
            raise ASRStreamError(f"连接实时 ASR 服务失败：{exc}") from exc

    async def start_task(self, language_hints: Optional[list[str]] = None) -> str:
        if self._connection is None:
            raise ASRStreamError("实时 ASR 连接尚未建立")
        if self.task_id is not None:
            raise ASRStreamError("当前识别任务尚未结束")

        self.task_id = str(uuid.uuid4())
        self.finishing = False
        parameters: dict[str, Any] = {
            "format": "pcm",
            "sample_rate": ASR_REALTIME_SAMPLE_RATE,
            "semantic_punctuation_enabled": False,
            "max_sentence_silence": ASR_VAD_SILENCE_MS,
            "punctuation_prediction_enabled": True,
        }
        if language_hints:
            parameters["language_hints"] = language_hints

        await self._send_json(
            {
                "header": {
                    "action": "run-task",
                    "task_id": self.task_id,
                    "streaming": "duplex",
                },
                "payload": {
                    "task_group": "audio",
                    "task": "asr",
                    "function": "recognition",
                    "model": ASR_REALTIME_MODEL,
                    "parameters": parameters,
                    "input": {},
                },
            }
        )
        return self.task_id

    async def send_audio(self, audio_frame: bytes) -> None:
        if self._connection is None or self.task_id is None or self.finishing:
            return
        try:
            await self._connection.send(audio_frame)
        except Exception as exc:
            raise ASRStreamError(f"发送实时音频失败：{exc}") from exc

    async def finish_task(self) -> None:
        if self._connection is None or self.task_id is None or self.finishing:
            return
        self.finishing = True
        await self._send_json(
            {
                "header": {
                    "action": "finish-task",
                    "task_id": self.task_id,
                    "streaming": "duplex",
                },
                "payload": {"input": {}},
            }
        )

    async def receive_event(self) -> dict[str, Any]:
        if self._connection is None:
            raise ASRStreamError("实时 ASR 连接尚未建立")
        try:
            raw = await self._connection.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ASRStreamError("实时 ASR 返回了无法解析的数据") from exc
        except Exception as exc:
            raise ASRStreamError(f"实时 ASR 连接中断：{exc}") from exc

    def reset_task(self) -> None:
        self.task_id = None
        self.finishing = False

    async def close(self) -> None:
        if self._connection is None:
            return
        connection, self._connection = self._connection, None
        self.reset_task()
        try:
            await connection.close()
        except Exception:
            pass

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._connection is None:
            raise ASRStreamError("实时 ASR 连接尚未建立")
        try:
            await self._connection.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            raise ASRStreamError(f"发送实时 ASR 指令失败：{exc}") from exc
