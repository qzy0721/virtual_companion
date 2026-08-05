# -*- coding: UTF-8 -*-
"""
Pydantic 请求/响应模型
"""

import re

from pydantic import BaseModel, Field, SecretStr, field_validator
from typing import Optional, List


_ACTION_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACTION_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")


def normalize_action_name(value: object) -> str:
    """将动作标签统一成前端动画键使用的 snake_case。"""
    action = str(value or "none").strip()
    action = _ACTION_CAMEL_BOUNDARY.sub("_", action)
    action = _ACTION_SEPARATOR.sub("_", action)
    return action.strip("_").lower() or "none"


# ============================================================
# 角色相关
# ============================================================

class RoleCreateRequest(BaseModel):
    """创建角色请求"""
    role_name: str = Field(..., min_length=1, max_length=50, description="角色名称")


class RoleResponse(BaseModel):
    """角色信息响应"""
    role_name: str
    system_prompt: str
    reference_audio_path: Optional[str] = None
    voice_id: Optional[str] = None
    voice_provider: Optional[str] = None
    oss_url: Optional[str] = None
    target_model: Optional[str] = None
    timestamp: Optional[str] = None


class RoleListResponse(BaseModel):
    """角色列表响应"""
    roles: List[str]


# ============================================================
# 对话相关
# ============================================================

class ChatRequest(BaseModel):
    """对话请求"""
    role_name: str = Field(..., description="角色名称")
    message: str = Field(..., min_length=1, description="用户消息")


class ChatStreamEvent(BaseModel):
    """SSE 流式事件"""
    type: str = Field(..., description="事件类型: text / audio / done / error")
    data: str = Field(default="", description="文本内容 或 base64音频 或 错误信息")


# ============================================================
# 表情控制相关
# ============================================================

class ExpressionRequest(BaseModel):
    """表情分析请求"""
    sentences: List[str] = Field(..., min_length=1, description="待分析的句子列表")
    available_expressions: List[str] = Field(..., min_length=1, description="可选的表情标签列表")
    available_actions: List[str] = Field(default_factory=lambda: ["none"], description="可选的动作标签列表")

    @field_validator("available_actions")
    @classmethod
    def normalize_available_actions(cls, actions: List[str]) -> List[str]:
        normalized = []
        for action in actions:
            action_name = normalize_action_name(action)
            if action_name not in normalized:
                normalized.append(action_name)
        if "none" not in normalized:
            normalized.insert(0, "none")
        return normalized


class ExpressionItem(BaseModel):
    """单个句子的表情"""
    sentence_index: int = Field(..., description="句子索引")
    expression: str = Field(..., description="表情标签")
    intensity: float = Field(..., ge=0.0, le=1.0, description="表情强度 0-1")
    action: Optional[str] = Field(default="none", description="身体动作标签")

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, action: object) -> str:
        return normalize_action_name(action)


class ExpressionResponse(BaseModel):
    """表情分析响应"""
    expressions: List[ExpressionItem] = Field(..., description="每个句子的表情序列")


# ============================================================
# ASR 相关
# ============================================================

class ASRResponse(BaseModel):
    """语音识别响应"""
    text: str = Field(default="", description="识别文本")
    error: Optional[str] = Field(default=None, description="错误信息")


# ============================================================
# 本地服务设置相关
# ============================================================

class SecretSettingStatus(BaseModel):
    """仅暴露密钥是否已配置及其掩码，不返回原始值。"""
    configured: bool
    hint: Optional[str] = None


class SettingsStatusResponse(BaseModel):
    """设置页面初始化所需的非敏感配置。"""
    llm_api_key: SecretSettingStatus
    llm_api_url: str
    llm_model: str
    dashscope_api_key: SecretSettingStatus
    dashscope_workspace_id: str
    default_tts_provider: str
    oss_access_key_id: SecretSettingStatus
    oss_access_key_secret: SecretSettingStatus
    oss_endpoint: str
    oss_bucket_name: str
    server_instance_id: str


class SettingsUpdateRequest(BaseModel):
    """本机设置页提交的配置；SecretStr 避免密钥意外出现在日志中。"""
    llm_api_key: Optional[SecretStr] = None
    llm_api_url: str = Field(..., min_length=1, max_length=2048)
    llm_model: str = Field(..., min_length=1, max_length=256)
    dashscope_api_key: Optional[SecretStr] = None
    dashscope_workspace_id: str = Field(default="", max_length=256)
    default_tts_provider: str = Field(default="cosyvoice", pattern="^(cosyvoice|qwen_tts)$")
    oss_access_key_id: Optional[SecretStr] = None
    oss_access_key_secret: Optional[SecretStr] = None
    oss_endpoint: str = Field(default="oss-cn-shanghai.aliyuncs.com", max_length=512)
    oss_bucket_name: str = Field(default="cosyvoice-reference-voice", max_length=256)


class SettingsUpdateResponse(BaseModel):
    """保存设置后的状态，不含任何原始密钥。"""
    status: str
    restart_required: bool
    message: str
