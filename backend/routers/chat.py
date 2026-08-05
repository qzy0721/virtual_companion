# -*- coding: UTF-8 -*-
"""
对话 API 路由（SSE 流式）。
"""

import os
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatRequest, ExpressionRequest, ExpressionResponse
from backend.services.chat_service import get_chat_service
from backend.services.expression_service import get_expression_service
from backend.routers.history import _load as load_history, append_turn, _save as save_history
from config import MAX_HISTORY

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("")
async def chat(request: ChatRequest):
    """
    发送消息，流式返回 LLM 文本 + TTS 音频。

    响应格式: Server-Sent Events (SSE)
      事件类型:
        - "delta" : JSON { "type": "delta", "data": "增量文本..." }
        - "text"  : JSON { "type": "text", "data": "完整回复文本" }
        - "sentence" : JSON { "type": "sentence", "data": "完整句子" }
        - "done"  : JSON { "type": "done", "data": "完整回复文本" }
        - "error" : JSON { "type": "error", "data": "错误信息" }
    """
    service = get_chat_service()

    async def event_generator():
        try:
            # 从文件加载该角色的最近历史
            all_history = load_history()
            role_history = [h for h in all_history if h.get("role_name") == request.role_name]
            messages_list = []
            for h in role_history[-MAX_HISTORY:]:  # 最近10轮
                for m in h.get("messages", []):
                    if m["role"] in ("user", "assistant"):
                        messages_list.append({"role": m["role"], "content": m["content"]})

            async for event_type, data in service.chat_stream(
                role_name=request.role_name,
                user_message=request.message,
                history=messages_list,
            ):
                if event_type == "delta":
                    yield f"event: delta\ndata: {json.dumps({'type': 'delta', 'data': data}, ensure_ascii=False)}\n\n"
                elif event_type == "text":
                    yield f"event: text\ndata: {json.dumps({'type': 'text', 'data': data}, ensure_ascii=False)}\n\n"
                elif event_type == "sentence":
                    yield f"event: sentence\ndata: {json.dumps({'type': 'sentence', 'data': data}, ensure_ascii=False)}\n\n"
                elif event_type == "done":
                    full_text = data
                    # 持久化到文件
                    append_turn(request.role_name, request.message, full_text)

                    yield f"event: done\ndata: {json.dumps({'type': 'done', 'data': full_text}, ensure_ascii=False)}\n\n"
                elif event_type == "error":
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'data': data}, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/history")
async def clear_history():
    """清空对话历史"""
    save_history([])
    return {"status": "ok", "message": "对话历史已清空"}


@router.post("/expressions", response_model=ExpressionResponse)
async def analyze_expressions(request: ExpressionRequest):
    """
    分析句子情感，返回表情序列。

    前端拿到 chat SSE 的 sentence 事件后，收集所有句子，
    结合 VRM 模型支持的表情列表，调用此接口获取表情序列。
    然后前端将句子 TTS 时长填入 duration_ms，驱动 VRM 表情动画。

    入参：
      - sentences:             LLM 生成的句子列表
      - available_expressions: VRM 模型支持的表情标签（如 ["happy","sad","angry","surprised","neutral"]）

    出参：
      - expressions: 每个句子对应的表情及强度
    """
    service = get_expression_service()
    expressions = await service.analyze(
        sentences=request.sentences,
        available_expressions=request.available_expressions,
        available_actions=request.available_actions,
    )
    return ExpressionResponse(expressions=expressions)
