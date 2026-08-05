# api.py

from threading import Lock

from openai import OpenAI
from config import LLM_API_KEY, LLM_API_URL, LLM_MODEL


LLM_ERROR_PREFIX = "\n[ERROR] "
_client = None
_client_lock = Lock()


class LLMConfigurationError(RuntimeError):
    """LLM 尚未完成必要配置。"""


def _is_configured(value):
    clean = (value or "").strip()
    return bool(clean) and not clean.upper().startswith("YOUR_")


def _get_client():
    """按需创建客户端，让未配置状态也能先进入服务设置页。"""
    global _client
    if _client is not None:
        return _client

    missing = []
    if not _is_configured(LLM_API_KEY):
        missing.append("LLM API Key")
    if not _is_configured(LLM_API_URL):
        missing.append("LLM API 地址")
    if not _is_configured(LLM_MODEL):
        missing.append("LLM 模型")
    if missing:
        raise LLMConfigurationError(
            f"LLM 配置不完整（缺少{'、'.join(missing)}），请前往“服务设置”填写后保存。"
        )

    with _client_lock:
        if _client is None:
            _client = OpenAI(
                api_key=LLM_API_KEY.strip(),
                base_url=LLM_API_URL.strip(),
            )
    return _client


def call_llm_stream(messages):
    """
    流式调用 LLM，返回生成内容流（对话用）
    :param messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
    :return: yield 每个流式返回的文本片段
    """
    try:
        response = _get_client().chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            stream=True,
            extra_body={"thinking": {"type": "disabled"}},
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except LLMConfigurationError as e:
        yield f"{LLM_ERROR_PREFIX}{e}"
    except Exception as e:
        yield f"{LLM_ERROR_PREFIX}{type(e).__name__}: {e}"


def call_llm(messages):
    """
    非流式调用 LLM，返回完整响应文本（表情服务等用）
    :param messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
    :return: 完整的响应文本
    """
    response = _get_client().chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        stream=False,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return response.choices[0].message.content


# 向后兼容别名
call_qwen_stream = call_llm_stream
