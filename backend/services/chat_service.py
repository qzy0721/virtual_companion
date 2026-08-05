# -*- coding: UTF-8 -*-
"""
对话编排服务：LLM 流式生成 + TTS 分句合成。
"""

import os
import re
import asyncio
import threading
from typing import AsyncGenerator, Optional, Tuple

from config import MAX_BUFFER_LENGTH
from api import LLM_ERROR_PREFIX, call_qwen_stream
from MemoryGenerate import MemoryGenerate
from PromptOptimizer import PromptOptimizer


class ChatService:
    """对话编排服务"""

    def __init__(self):
        self._memory: Optional[MemoryGenerate] = None

    @property
    def memory(self) -> MemoryGenerate:
        if self._memory is None:
            self._memory = MemoryGenerate()
        return self._memory

    # ------------------------------------------------------------------
    # 对话流
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        role_name: str,
        user_message: str,
        history: Optional[list] = None,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """
        流式对话：LLM 生成文本 + 分句 TTS 合成。

        Args:
            role_name:     角色名称
            user_message:  用户消息
            history:       历史消息列表（可选）

        Yields:
            (event_type, data) 元组:
              - ("delta", str)    — 新生成的文本片段
              - ("text", str)     — 完整响应文本（完成时同步）
              - ("sentence", str) — 检测到的完整句子（前端应调用 /api/tts 合成）
              - ("done", str)     — 完成信号（含完整响应文本）
              - ("error", str)    — 错误信息
        """
        # 1. 加载角色系统提示词
        system_prompt = PromptOptimizer.get_prompt_by_role(role_name)
        if not system_prompt:
            yield ("error", f"角色 '{role_name}' 不存在")
            return

        # 2. 加载长期记忆
        try:
            memories = self.memory.get_memories(limit=5)
            if memories:
                memory_text = "\n\n【长期记忆】\n" + "\n".join(
                    f"- {m['content']}" for m in memories
                )
                system_prompt += memory_text
        except Exception as e:
            print(f"⚠️ 加载记忆失败: {e}")

        # 3. 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # 5. LLM 流式生成 + 分句检测
        full_response = ""
        buffer = ""

        # 将同步生成器包装为异步
        async_gen = self._async_wrap_qwen(messages)

        async for chunk in async_gen:
            if chunk.startswith(LLM_ERROR_PREFIX):
                yield ("error", chunk[len(LLM_ERROR_PREFIX):].strip())
                return

            # 过滤不可打印字符
            clean = ''.join(c for c in chunk if c.isprintable() or c in '\n')
            if not clean:
                continue

            full_response += clean
            buffer += clean

            yield ("delta", clean)

            # 分句 → 推送 sentence 事件（前端收到后自行调 /api/tts）
            sentences, buffer = self._split_sentences(buffer)
            for sentence in sentences:
                cleaned = self._clean_for_tts(sentence)
                if cleaned:
                    yield ("sentence", cleaned)

        # 6. 推送最终文本
        if full_response:
            yield ("text", full_response)
        else:
            yield ("text", "(无响应)")

        # 7. 处理剩余缓冲区
        if buffer.strip():
            cleaned = self._clean_for_tts(buffer.strip())
            if cleaned:
                yield ("sentence", cleaned)

        # 8. 完成
        yield ("done", full_response)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    async def _async_wrap_qwen(self, messages: list) -> AsyncGenerator[str, None]:
        """将同步 call_qwen_stream 包装为异步生成器"""
        event_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def publish(event_type: str, data=None) -> None:
            try:
                loop.call_soon_threadsafe(event_queue.put_nowait, (event_type, data))
            except RuntimeError:
                # 客户端断开后，事件循环可能已关闭。
                pass

        def runner():
            try:
                for chunk in call_qwen_stream(messages):
                    publish('chunk', chunk)
                publish('done')
            except Exception as error:
                publish('error', error)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        while True:
            msg_type, data = await event_queue.get()

            if msg_type == 'chunk':
                yield data
            elif msg_type == 'done':
                break
            elif msg_type == 'error':
                if isinstance(data, BaseException):
                    raise data
                raise RuntimeError(str(data))

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        """
        清理文本中不适合 TTS 合成的内容：
        - 去除省略号（... …… …），避免提交无实际内容的 TTS 文本
        - 去除纯标点/空白（TTS 无法合成无实际内容的文本）
        """
        # 去除各类省略号：连续句点 ...、中文省略号 ……、Unicode 省略号 …
        cleaned = re.sub(r'\.{2,}|…{1,}|⋯{1,}', '', text)
        # 检查是否还有实际内容（非标点、非空白）
        if not re.search(r'[\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', cleaned):
            return ""
        return cleaned.strip()

    @staticmethod
    def _split_sentences(buffer: str) -> Tuple[list, str]:
        """
        按句子分割缓冲区。
        返回: (待播放句子列表, 剩余缓冲区)
        """
        sentences = re.split(r'(?<=[.!?。！？])', buffer)
        if len(sentences) > 1:
            to_speak = [s.strip() for s in sentences[:-1] if s.strip()]
            return to_speak, sentences[-1]

        if len(buffer) >= MAX_BUFFER_LENGTH:
            return [buffer[:MAX_BUFFER_LENGTH].strip()], buffer[MAX_BUFFER_LENGTH:]

        return [], buffer


# 单例
_chat_service: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    """获取 ChatService 单例"""
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service
