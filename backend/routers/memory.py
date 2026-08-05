# -*- coding: UTF-8 -*-
"""
长期记忆 API 路由。
"""

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from MemoryGenerate import MemoryGenerate

router = APIRouter(prefix="/api/memory", tags=["memory"])

# MemoryGenerate 单例
_mem_gen: MemoryGenerate = None


def _get_mem() -> MemoryGenerate:
    global _mem_gen
    if _mem_gen is None:
        _mem_gen = MemoryGenerate()
    return _mem_gen


@router.get("")
async def get_memories():
    """获取所有长期记忆"""
    mem = _get_mem()
    memories = mem.get_memories()
    return {"memories": memories, "total": len(memories)}


@router.post("/generate")
async def generate_memory():
    """
    触发记忆生成：从对话历史中提取关键信息，生成长期记忆。

    调用后会调用 Qwen API，需要几秒到十几秒。
    """
    try:
        import asyncio
        mem = _get_mem()
        # MemoryGenerate.generate_memory 是同步方法，放线程执行
        result = await asyncio.to_thread(mem.generate_memory, max_history_messages=200)
        if result:
            return {"status": "ok", "memory": result}
        else:
            return {"status": "ok", "memory": None, "message": "无可提取的记忆（历史太少或生成失败）"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"记忆生成失败: {e}")


@router.delete("")
async def clear_memories():
    """清空所有长期记忆"""
    mem = _get_mem()
    mem.clear_memories()
    return {"status": "ok", "cleared": "all"}
