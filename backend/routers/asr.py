# -*- coding: UTF-8 -*-
"""
语音识别 API 路由。
"""

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.asr_stream_service import ASRStreamError, DashScopeRealtimeASRSession
from config import ASR_MAX_FRAME_BYTES, ASR_MAX_SESSION_SECONDS

router = APIRouter(prefix="/api/asr", tags=["asr"])


def _merge_transcript(prefix: str, suffix: str) -> str:
    """追加未定稿片段，并消除与既有文本的重叠。"""
    if not suffix or prefix.endswith(suffix):
        return prefix
    for overlap in range(min(len(prefix), len(suffix)), 0, -1):
        if prefix.endswith(suffix[:overlap]):
            return prefix + suffix[overlap:]
    return prefix + suffix


@router.websocket("/stream")
async def realtime_speech_to_text(websocket: WebSocket):
    """浏览器 PCM 流与 DashScope 实时 ASR 的双 WebSocket 桥接。"""
    await websocket.accept()
    session = DashScopeRealtimeASRSession()
    relay_task: Optional[asyncio.Task] = None
    started_at = 0.0
    final_segments: list[str] = []
    partial_segment = ""

    async def send_message(message_type: str, **data: object) -> None:
        await websocket.send_json({"type": message_type, **data})

    async def relay_results() -> None:
        nonlocal relay_task, partial_segment
        try:
            while session.is_task_active:
                event = await session.receive_event()
                header = event.get("header") or {}
                payload = event.get("payload") or {}
                event_name = header.get("event")

                if event_name == "result-generated":
                    sentence = ((payload.get("output") or {}).get("sentence") or {})
                    if sentence.get("heartbeat"):
                        continue
                    text = str(sentence.get("text") or "").strip()
                    if not text:
                        continue
                    if sentence.get("sentence_end"):
                        final_segments.append(text)
                        partial_segment = ""
                        await send_message("final", text=text)
                    else:
                        partial_segment = text
                        await send_message("partial", text=text)
                elif event_name == "task-finished":
                    text = _merge_transcript("".join(final_segments), partial_segment)
                    await send_message("done", text=text)
                    partial_segment = ""
                    session.reset_task()
                    return
                elif event_name == "task-failed":
                    message = header.get("error_message") or "实时语音识别任务失败"
                    await send_message("error", message=message)
                    await session.close()
                    return
        except ASRStreamError as exc:
            await send_message("error", message=str(exc))
            await session.close()
        finally:
            relay_task = None

    try:
        while True:
            received = await websocket.receive()
            if received["type"] == "websocket.disconnect":
                break

            if (
                session.is_task_active
                and not session.finishing
                and time.monotonic() - started_at > ASR_MAX_SESSION_SECONDS
            ):
                await send_message("error", message="本次语音输入时间过长，请重新开始")
                await session.finish_task()
                continue

            audio_frame = received.get("bytes")
            if audio_frame is not None:
                if not session.is_task_active or session.finishing:
                    continue
                if len(audio_frame) > ASR_MAX_FRAME_BYTES:
                    await send_message("error", message="音频分片过大，已忽略")
                    continue
                await session.send_audio(audio_frame)
                continue

            raw_text = received.get("text")
            if raw_text is None:
                continue
            try:
                control = json.loads(raw_text)
            except json.JSONDecodeError:
                await send_message("error", message="无法解析语音控制指令")
                continue

            command = control.get("type")
            if command == "start":
                if session.is_task_active or relay_task is not None:
                    await send_message("error", message="上一段语音尚未结束")
                    continue

                hints = control.get("language_hints")
                if not isinstance(hints, list) or not all(isinstance(item, str) for item in hints):
                    hints = None
                final_segments = []
                partial_segment = ""
                try:
                    await session.connect()
                    await session.start_task(hints)
                except ASRStreamError as exc:
                    await session.close()
                    await send_message("error", message=str(exc))
                    continue

                try:
                    first_event = await asyncio.wait_for(session.receive_event(), timeout=10)
                except asyncio.TimeoutError:
                    await session.close()
                    await send_message("error", message="实时语音识别连接超时")
                    continue

                header = first_event.get("header") or {}
                if header.get("event") != "task-started":
                    message = header.get("error_message") or "实时语音识别未能启动"
                    await session.close()
                    await send_message("error", message=message)
                    continue

                started_at = time.monotonic()
                await send_message("ready")
                relay_task = asyncio.create_task(relay_results())
            elif command == "stop":
                if session.is_task_active:
                    await session.finish_task()
            else:
                await send_message("error", message="未知语音控制指令")
    except WebSocketDisconnect:
        pass
    except ASRStreamError as exc:
        try:
            await send_message("error", message=str(exc))
        except Exception:
            pass
    finally:
        if relay_task is not None:
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                pass
        await session.close()
