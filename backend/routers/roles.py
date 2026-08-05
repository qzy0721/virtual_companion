# -*- coding: UTF-8 -*-
"""
角色管理 API 路由。
"""

import os
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

from backend.models.schemas import RoleResponse, RoleListResponse
from backend.services.role_service import get_role_service

router = APIRouter(prefix="/api/roles", tags=["roles"])


@router.get("", response_model=RoleListResponse)
async def list_roles():
    """列出所有角色"""
    service = get_role_service()
    roles = service.list_roles()
    return RoleListResponse(roles=roles)


@router.get("/default/status")
async def get_default_role_status():
    """返回本地默认角色及其语音启用状态。"""
    from config import DEFAULT_ROLE_NAME

    service = get_role_service()
    role = service.get_role(DEFAULT_ROLE_NAME)
    if not role:
        raise HTTPException(status_code=404, detail="默认角色不存在")
    return {
        "role_name": DEFAULT_ROLE_NAME,
        "voice_enabled": bool(role.get("voice_id")),
    }


@router.post("/default/enable-voice", response_model=RoleResponse)
async def enable_default_role_voice():
    """按需为本地默认角色注册所选引擎的复刻音色。"""
    from config import DEFAULT_ROLE_NAME

    service = get_role_service()
    try:
        role = service.enable_voice(DEFAULT_ROLE_NAME)
        return RoleResponse(**role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启用默认角色语音失败: {e}") from e


@router.get("/{role_name}", response_model=RoleResponse)
async def get_role(role_name: str):
    """获取指定角色详情"""
    service = get_role_service()
    info = service.get_role(role_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"角色 '{role_name}' 不存在")
    return RoleResponse(**info)


@router.post("", response_model=RoleResponse)
async def create_role(
    role_name: str = Form(..., description="角色名称"),
    voice_provider: str = Form("cosyvoice", description="音色引擎: cosyvoice / qwen_tts"),
    audio: UploadFile = File(None, description="参考音频文件（WAV）"),
):
    """
    创建新角色。

    流程：生成系统提示词 → 从本地参考音频创建所选引擎音色 → 持久化。
    """
    service = get_role_service()

    # 读取上传的音频
    audio_bytes = None
    audio_filename = None
    if audio:
        audio_bytes = await audio.read()
        audio_filename = audio.filename

    try:
        info = service.create_role(
            role_name,
            audio_bytes,
            audio_filename,
            voice_provider=voice_provider,
        )
        return RoleResponse(**info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建角色失败: {e}")


@router.post("/create-with-baike", response_model=RoleResponse)
async def create_role_with_baike(
    role_name: str = Form(..., description="角色名称"),
    baike_query: str = Form("", description="百度百科词条名或URL，为空则用角色名搜索"),
    voice_provider: str = Form("cosyvoice", description="音色引擎: cosyvoice / qwen_tts"),
    audio: UploadFile = File(None, description="参考音频文件（WAV）"),
):
    """
    创建新角色（带百度百科知识补充）。

    与 POST /api/roles 的区别：先爬取百度百科词条，将百科内容作为参考资料
    喂给 LLM 生成系统提示词，使角色掌握更丰富的背景知识。

    流程：爬取百科 → 生成系统提示词（含百科知识）→ 创建所选引擎音色 → 持久化。

    百科爬取失败时不阻塞创建，回退到无百科的普通流程。
    """
    service = get_role_service()

    # 读取上传的音频
    audio_bytes = None
    audio_filename = None
    if audio:
        audio_bytes = await audio.read()
        audio_filename = audio.filename

    try:
        info = service.create_role(
            role_name, audio_bytes, audio_filename,
            baike_query=baike_query,
            voice_provider=voice_provider,
        )
        return RoleResponse(**info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建角色失败: {e}")


@router.post("/register", response_model=RoleResponse)
async def register_role(
    role_name: str = Form(..., description="角色名称"),
    system_prompt: str = Form(..., description="系统提示词（用户手写）"),
    voice_provider: str = Form("cosyvoice", description="音色引擎: cosyvoice / qwen_tts"),
    audio: UploadFile = File(None, description="参考音频文件（WAV）"),
):
    """
    注册新角色（用户手写提示词，跳过 LLM 生成）。

    流程：用户提供的提示词 → 创建所选引擎音色 → 持久化。
    适合用户已经知道要怎么设定角色人格的场景。
    """
    service = get_role_service()

    # 保存上传的音频到本地
    audio_path = None
    if audio:
        audio_bytes = await audio.read()
        audio_filename = audio.filename
        audio_path = service._save_audio_locally(role_name, audio_bytes, audio_filename)

    # 无上传音频则用默认
    if not audio_path:
        from config import REFERENCE_AUDIO_PATH
        if os.path.isfile(REFERENCE_AUDIO_PATH):
            audio_path = REFERENCE_AUDIO_PATH
        else:
            raise HTTPException(status_code=400, detail="未提供参考音频，且默认音频文件不存在")

    try:
        info = service.register_role(
            role_name=role_name,
            system_prompt=system_prompt,
            local_audio_path=audio_path,
            voice_provider=voice_provider,
        )
        return RoleResponse(**info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"注册角色失败: {e}")


@router.delete("/{role_name}")
async def delete_role(role_name: str):
    """删除角色及其云端复刻音色。"""
    service = get_role_service()
    success = service.delete_role(role_name)
    if not success:
        raise HTTPException(status_code=404, detail=f"角色 '{role_name}' 不存在")
    return {"status": "ok", "message": f"角色 '{role_name}' 已删除"}
