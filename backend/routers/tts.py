# -*- coding: UTF-8 -*-
"""
TTS 语音合成 API 路由（独立于对话）。
"""

import os
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import TTS_TEMP_DIR
from backend.services.role_service import get_role_service

router = APIRouter(prefix="/api/tts", tags=["tts"])

# 确保临时目录存在
os.makedirs(TTS_TEMP_DIR, exist_ok=True)

# 控制测试环境中的并发和云端限流，句级音频仍按前端队列顺序播放。
_tts_lock = asyncio.Lock()


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="要合成的文本")
    role_name: str = Field(..., min_length=1, description="角色名称")


class TTSResponse(BaseModel):
    url: str = Field(..., description="音频文件的访问 URL")
    filename: str = Field(..., description="音频文件名")
    duration_ms: float = Field(default=0, description="音频时长（毫秒）")


@router.post("", response_model=TTSResponse)
async def synthesize(request: TTSRequest):
    """
    合成文本为语音，保存到本地临时文件，返回可访问的 URL。

    前端获取 URL 后可用 <audio> 或 Audio 对象播放。
    """
    # 1. 获取角色音色配置，并按角色记录自动选择 TTS 引擎。
    service = get_role_service()
    role_info = service.get_role(request.role_name)
    voice_id = role_info.get("voice_id") if role_info else None
    if not voice_id:
        print(f"[TTS] 角色 '{request.role_name}' 没有 voice_id，跳过语音合成")
        raise HTTPException(
            status_code=400,
            detail=f"角色 '{request.role_name}' 未配置音色，请检查服务设置与 server.log 后重试注册",
        )

    # 2. TTS 合成（串行化，避免并发的 WebSocket 冲突）
    tts = service.get_tts_client(role_info)
    async with _tts_lock:
        try:
            audio = await asyncio.to_thread(
                tts.synthesize, request.text, voice_id
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"TTS 合成失败: {e}")

    if not audio:
        raise HTTPException(status_code=500, detail="TTS 合成返回空数据")

    # 3. 保存到临时文件
    import uuid
    filename = f"tts_{uuid.uuid4().hex[:8]}{audio.extension}"
    filepath = os.path.join(TTS_TEMP_DIR, filename)

    with open(filepath, 'wb') as f:
        f.write(audio.data)

    url = f"/tts_audio/{filename}"
    print(f"[INFO] TTS 已保存: {filename} ({len(audio.data)} bytes, {audio.duration_ms:.0f}ms)")

    return TTSResponse(url=url, filename=filename, duration_ms=audio.duration_ms)
