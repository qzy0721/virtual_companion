# -*- coding: UTF-8 -*-
"""
表情分析服务：对 LLM 生成的文本做情感分析，输出结构化表情序列。
用于驱动前端 VRM 模型的面部表情动画。
"""

import os
import json
import re
import asyncio
from typing import List, Optional

from api import call_llm
from config import EXPRESSION_ANALYSIS_PROMPT
from backend.models.schemas import normalize_action_name


class ExpressionService:
    """表情分析服务：将句子列表映射为表情序列"""

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def analyze(
        self,
        sentences: List[str],
        available_expressions: List[str],
        available_actions: Optional[List[str]] = None,
    ) -> List[dict]:
        """
        分析句子情感，返回表情+动作序列。

        Args:
            sentences:             待分析的句子列表
            available_expressions: 可选表情标签列表
            available_actions:     可选动作标签列表（默认 ["none"]）

        Returns:
            [{"sentence_index": 0, "expression": "happy", "intensity": 0.8, "action": "clapping"}, ...]
        """
        if not sentences:
            return []

        if "neutral" not in available_expressions:
            available_expressions = list(available_expressions) + ["neutral"]

        actions = available_actions or ["none"]
        if "none" not in actions:
            actions = list(actions) + ["none"]

        try:
            prompt = self._build_prompt(sentences, available_expressions, actions)
            raw = await asyncio.to_thread(self._call_llm, prompt)
            parsed = self._parse_response(raw, len(sentences), available_expressions, actions)
            print(
                "[ExpressionService] 分析结果: "
                + json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            )
            return parsed
        except Exception as e:
            print(f"[ExpressionService] 分析失败: {e}")
            return self._fallback(sentences)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_prompt(self, sentences: List[str], expressions: List[str], actions: List[str]) -> str:
        """构建发给 LLM 的提示词"""
        sentences_text = "\n".join(
            f"{i}: {s}" for i, s in enumerate(sentences)
        )
        return EXPRESSION_ANALYSIS_PROMPT.format(
            available_expressions=", ".join(expressions),
            available_actions=", ".join(actions),
            sentences=sentences_text,
        )

    def _call_llm(self, prompt: str) -> str:
        """同步调用 LLM，返回完整响应文本"""
        messages = [
            {"role": "system", "content": "你是一个专业的情感分析助手，请只返回 JSON 数组。"},
            {"role": "user", "content": prompt},
        ]

        return call_llm(messages)

    def _parse_response(
        self,
        raw: str,
        expected_count: int,
        available: List[str],
        actions: List[str],
    ) -> List[dict]:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return self._validate_and_fix(data, expected_count, available, actions)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return self._validate_and_fix(data, expected_count, available, actions)
            except json.JSONDecodeError:
                pass
        print(f"[ExpressionService] 无法解析 LLM 响应: {raw[:200]}")
        return self._fallback([""] * expected_count)

    def _validate_and_fix(
        self,
        data: List[dict],
        expected_count: int,
        available: List[str],
        actions: List[str],
    ) -> List[dict]:
        """校验并修复表情+动作序列"""
        available_map = {e.lower(): e for e in available}
        actions_map = {}
        for available_action in actions:
            canonical_action = normalize_action_name(available_action)
            actions_map[canonical_action] = canonical_action
            actions_map[canonical_action.replace("_", "")] = canonical_action

        indexed = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("sentence_index", -1))
            except (TypeError, ValueError):
                continue
            expr_raw = str(item.get("expression", "neutral")).strip()
            try:
                intensity = float(item.get("intensity", 0.5))
            except (TypeError, ValueError):
                intensity = 0.5
            action_raw = normalize_action_name(item.get("action", "none"))

            expr_lower = expr_raw.lower()
            expr = available_map.get(expr_lower, "neutral")
            intensity = max(0.0, min(1.0, intensity))

            action = actions_map.get(
                action_raw,
                actions_map.get(action_raw.replace("_", ""), "none"),
            )

            indexed[idx] = {
                "sentence_index": idx,
                "expression": expr,
                "intensity": round(intensity, 2),
                "action": action,
            }

        result = []
        for i in range(expected_count):
            if i in indexed:
                result.append(indexed[i])
            else:
                result.append({
                    "sentence_index": i,
                    "expression": "neutral",
                    "intensity": 0.5,
                    "action": "none",
                })
        return result

    def _fallback(self, sentences: List[str]) -> List[dict]:
        return [
            {
                "sentence_index": i,
                "expression": "neutral",
                "intensity": 0.5,
                "action": "none",
            }
            for i in range(len(sentences))
        ]


# 单例
_expression_service: Optional[ExpressionService] = None


def get_expression_service() -> ExpressionService:
    global _expression_service
    if _expression_service is None:
        _expression_service = ExpressionService()
    return _expression_service
