# -*- coding: UTF-8 -*-
"""仅供本机设置页面使用的服务配置接口。"""

from __future__ import annotations

import ipaddress
import os
import shutil
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values, set_key
from fastapi import APIRouter, HTTPException, Request, Response, status

from backend.models.schemas import (
    SecretSettingStatus,
    SettingsStatusResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
)
from config import ENV_FILE


router = APIRouter(prefix="/api/settings", tags=["settings"])

_SERVER_INSTANCE_ID = uuid4().hex
_ENV_WRITE_LOCK = threading.Lock()
_DEFAULTS = {
    "LLM_API_URL": "https://api.deepseek.com/v1",
    "LLM_MODEL": "deepseek-v4-flash",
    "DASHSCOPE_WORKSPACE_ID": "",
    "DEFAULT_TTS_PROVIDER": "cosyvoice",
    "OSS_ENDPOINT": "oss-cn-shanghai.aliyuncs.com",
    "OSS_BUCKET_NAME": "cosyvoice-reference-voice",
}
_SECRET_KEYS = (
    "LLM_API_KEY",
    "DASHSCOPE_API_KEY",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
)


def _require_loopback(request: Request) -> None:
    """配置接口不能暴露给局域网访问者。"""
    client = request.client
    if client is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="设置接口只允许本机访问")

    try:
        client_ip = ipaddress.ip_address(client.host)
        if client_ip.version == 6 and client_ip.ipv4_mapped is not None:
            client_ip = client_ip.ipv4_mapped
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="设置接口只允许本机访问") from None

    if not client_ip.is_loopback:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="设置接口只允许本机访问")


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _read_env_values() -> dict[str, str | None]:
    """从 .env 读取配置，缺失项才回退到进程环境。"""
    file_values = dotenv_values(ENV_FILE)
    values: dict[str, str | None] = {}
    for key in (*_SECRET_KEYS, *_DEFAULTS):
        value = file_values.get(key)
        if value is None:
            value = os.getenv(key, _DEFAULTS.get(key, ""))
        values[key] = value
    return values


def _clean_value(value: str | None, field_name: str, *, required: bool = False, max_length: int = 2048) -> str:
    clean = (value or "").strip()
    if any(character in clean for character in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 不能包含换行符")
    if len(clean) > max_length:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 长度超出限制")
    if required and not clean:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"请填写 {field_name}")
    return clean


def _is_configured(value: str | None) -> bool:
    clean = (value or "").strip()
    return bool(clean) and not clean.upper().startswith("YOUR_")


def _secret_status(value: str | None) -> SecretSettingStatus:
    if not _is_configured(value):
        return SecretSettingStatus(configured=False)
    clean = value.strip()
    suffix = clean[-4:] if len(clean) > 4 else "****"
    return SecretSettingStatus(configured=True, hint=f"已配置（末尾 {suffix}）")


def _secret_input_value(value) -> str:
    return value.get_secret_value() if value is not None else ""


def _resolve_secret_update(
    key: str,
    field_name: str,
    submitted_value: str,
    existing_value: str | None,
) -> str | None:
    """空密钥表示保留已有值；首次配置则必须提供有效密钥。"""
    clean = _clean_value(submitted_value, field_name, max_length=2048)
    if clean:
        if not _is_configured(clean):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"请填写有效的 {field_name}")
        return clean
    if _is_configured(existing_value):
        return None
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"请填写 {field_name}")


def _resolve_optional_secret_update(submitted_value: str) -> str | None:
    clean = _clean_value(submitted_value, "OSS 密钥", max_length=2048)
    if not clean:
        return None
    if not _is_configured(clean):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写有效的 OSS 密钥")
    return clean


def _atomic_update_env(updates: dict[str, str]) -> None:
    """先在同目录临时文件中完成所有变更，再一次替换 .env。"""
    env_path = Path(ENV_FILE)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=env_path.parent)
    os.close(descriptor)
    temporary_env = Path(temporary_path)

    try:
        if env_path.exists():
            shutil.copyfile(env_path, temporary_env)
        else:
            temporary_env.write_text("", encoding="utf-8")

        for key, value in updates.items():
            set_key(temporary_env, key, value, quote_mode="auto")

        os.replace(temporary_env, env_path)
    except OSError as error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="配置文件保存失败") from error
    finally:
        if temporary_env.exists():
            temporary_env.unlink(missing_ok=True)


@router.get("/status", response_model=SettingsStatusResponse)
async def get_settings_status(request: Request, response: Response):
    _require_loopback(request)
    _set_no_store(response)
    values = _read_env_values()
    return SettingsStatusResponse(
        llm_api_key=_secret_status(values["LLM_API_KEY"]),
        llm_api_url=(values["LLM_API_URL"] or _DEFAULTS["LLM_API_URL"]).strip(),
        llm_model=(values["LLM_MODEL"] or _DEFAULTS["LLM_MODEL"]).strip(),
        dashscope_api_key=_secret_status(values["DASHSCOPE_API_KEY"]),
        dashscope_workspace_id=(values["DASHSCOPE_WORKSPACE_ID"] or "").strip(),
        default_tts_provider=(values["DEFAULT_TTS_PROVIDER"] or "cosyvoice").strip(),
        oss_access_key_id=_secret_status(values["OSS_ACCESS_KEY_ID"]),
        oss_access_key_secret=_secret_status(values["OSS_ACCESS_KEY_SECRET"]),
        oss_endpoint=(values["OSS_ENDPOINT"] or _DEFAULTS["OSS_ENDPOINT"]).strip(),
        oss_bucket_name=(values["OSS_BUCKET_NAME"] or _DEFAULTS["OSS_BUCKET_NAME"]).strip(),
        server_instance_id=_SERVER_INSTANCE_ID,
    )


@router.put("", response_model=SettingsUpdateResponse, status_code=status.HTTP_202_ACCEPTED)
async def update_settings(payload: SettingsUpdateRequest, request: Request, response: Response):
    _require_loopback(request)
    _set_no_store(response)

    with _ENV_WRITE_LOCK:
        values = _read_env_values()
        updates = {
            "LLM_API_URL": _clean_value(payload.llm_api_url, "LLM API 地址", required=True),
            "LLM_MODEL": _clean_value(payload.llm_model, "LLM 模型", required=True, max_length=256),
            "DASHSCOPE_WORKSPACE_ID": _clean_value(payload.dashscope_workspace_id, "Workspace ID", max_length=256),
            "DEFAULT_TTS_PROVIDER": payload.default_tts_provider,
            "OSS_ENDPOINT": _clean_value(payload.oss_endpoint, "OSS Endpoint", max_length=512),
            "OSS_BUCKET_NAME": _clean_value(payload.oss_bucket_name, "OSS Bucket", max_length=256),
        }

        for key, field_name, submitted in (
            ("LLM_API_KEY", "LLM API Key", _secret_input_value(payload.llm_api_key)),
            ("DASHSCOPE_API_KEY", "DashScope API Key", _secret_input_value(payload.dashscope_api_key)),
        ):
            secret_update = _resolve_secret_update(key, field_name, submitted, values[key])
            if secret_update is not None:
                updates[key] = secret_update

        for key, submitted in (
            ("OSS_ACCESS_KEY_ID", _secret_input_value(payload.oss_access_key_id)),
            ("OSS_ACCESS_KEY_SECRET", _secret_input_value(payload.oss_access_key_secret)),
        ):
            secret_update = _resolve_optional_secret_update(submitted)
            if secret_update is not None:
                updates[key] = secret_update

        file_values = dotenv_values(ENV_FILE)
        changed_updates = {
            key: value
            for key, value in updates.items()
            if file_values.get(key) != value
        }
        if not changed_updates:
            return SettingsUpdateResponse(
                status="unchanged",
                restart_required=False,
                message="配置没有变化，无需重启服务",
            )

        _atomic_update_env(changed_updates)

    # 开发态由 Uvicorn reload 监听 .env；打包态则由父启动器接住指定退出码后重启。
    schedule_restart = getattr(request.app.state, "schedule_restart", None)
    if callable(schedule_restart):
        schedule_restart()

    return SettingsUpdateResponse(
        status="saved",
        restart_required=True,
        message="配置已保存，服务正在重新加载",
    )
