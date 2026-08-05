# -*- coding: UTF-8 -*-
"""
桌宠 API 路由（融合自 AI 虚拟人助手项目）。

提供 Phase 1 桌宠窗口控制与驱动消息中转。
"""

import os
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import RESOURCE_DIR, USER_DATA_DIR
from backend.services.pet_service import get_pet_service

router = APIRouter(prefix="/api/pet", tags=["pet"])

# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class PetOpenRequest(BaseModel):
    width: Optional[int] = Field(default=None, ge=200, le=800)
    height: Optional[int] = Field(default=None, ge=300, le=1200)


class PetResizeRequest(BaseModel):
    width: int = Field(..., ge=200, le=800)
    height: int = Field(..., ge=300, le=1200)


class PetTransparentRequest(BaseModel):
    enabled: bool = True


class PetReadyRequest(BaseModel):
    expressions: list = Field(default_factory=list)
    actions: list = Field(default_factory=list)


class PetContextRequest(BaseModel):
    role_name: str = Field(default="", max_length=120)


class PetThemeRequest(BaseModel):
    theme: str = Field(default="wisteria", max_length=32)


class PetPreferencesRequest(BaseModel):
    width: int = Field(default=500, ge=360, le=800)
    height: int = Field(default=500, ge=360, le=800)
    idle_action: str = Field(default="idle", max_length=32)


# ---------------------------------------------------------------------------
# 窗口控制
# ---------------------------------------------------------------------------

def _pet_page_url(request: Request) -> str:
    """桌宠页面 URL（与后端同源，经 /app 静态挂载提供）"""
    port = getattr(request.app.state, "server_port", None) or request.url.port or 8000
    return f"http://127.0.0.1:{port}/app/pet.html?pet=1"


@router.post("/open")
async def open_pet(request: Request, body: PetOpenRequest = None):
    """创建或恢复桌宠悬浮窗口（CEF OSR + Win32 透明窗口）"""
    # 前端未构建时桌宠页面不存在，提前给出明确提示
    dist_dir = os.path.join(RESOURCE_DIR, "web", "dist")
    if not os.path.isfile(os.path.join(dist_dir, "pet.html")):
        raise HTTPException(status_code=409, detail="前端尚未构建（缺少 web/dist/pet.html），请先执行 npm run build")

    service = get_pet_service()
    body = body or PetOpenRequest()
    ok = service.open_pet(_pet_page_url(request), body.width, body.height)
    if not ok:
        raise HTTPException(status_code=503, detail="桌宠引擎不可用（CEF 初始化失败），主功能不受影响")
    return {"status": "ok"}


@router.post("/close")
async def close_pet():
    """隐藏桌宠窗口（不销毁）"""
    get_pet_service().close_pet()
    return {"status": "ok"}


@router.post("/quit")
async def quit_pet():
    """退出桌宠界面；CEF 线程待命，以便主界面再次弹出。"""
    get_pet_service().quit_pet()
    return {"status": "ok"}


@router.get("/status")
async def pet_status():
    """桌宠存活与就绪状态（前端轮询用）"""
    return get_pet_service().status()


@router.post("/resize")
async def resize_pet(body: PetResizeRequest):
    """调整桌宠窗口尺寸"""
    return get_pet_service().resize(body.width, body.height)


@router.post("/transparent")
async def set_pet_transparent(body: PetTransparentRequest):
    """设置桌宠背景透明/不透明"""
    get_pet_service().set_transparent(body.enabled)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 驱动消息中转
# ---------------------------------------------------------------------------

@router.post("/drive")
async def drive_pet(data: dict):
    """
    主窗口驱动消息中转：expression / volume / animation / reset / load_model。
    桌宠未就绪时仅缓存 load_model，其余消息丢弃。
    """
    delivered = get_pet_service().drive(data)
    return {"status": "ok", "delivered": delivered}


@router.post("/ready")
async def pet_page_ready(body: PetReadyRequest = None):
    """桌宠页面通知：VRM 舞台就绪（可附带表情/动作清单）"""
    get_pet_service().mark_page_ready(body.model_dump() if body else None)
    return {"status": "ok"}


@router.get("/capabilities")
async def pet_capabilities():
    """桌宠当前模型的表情/动作清单（供主窗口表情分析使用）"""
    return get_pet_service().get_capabilities()


@router.get("/context")
async def pet_context():
    """返回桌宠对话所使用的当前角色。"""
    return get_pet_service().get_context()


@router.post("/context")
async def set_pet_context(body: PetContextRequest):
    """由主窗口同步当前选中的角色。"""
    return get_pet_service().set_role(body.role_name)


@router.post("/theme")
async def set_pet_theme(body: PetThemeRequest):
    """同步五套前端主题到独立 CEF 桌宠页面。"""
    return get_pet_service().set_theme(body.theme)


@router.get("/preferences")
async def get_pet_preferences():
    """返回桌宠舞台尺寸和默认待机动作。"""
    return get_pet_service().get_preferences()


@router.put("/preferences")
async def set_pet_preferences(body: PetPreferencesRequest):
    """保存并立即应用桌宠舞台尺寸与默认待机动作。"""
    return get_pet_service().set_preferences(body.width, body.height, body.idle_action)


def _current_model_path() -> Path:
    return Path(USER_DATA_DIR) / "pet_models" / "current.vrm"


@router.post("/model")
async def upload_pet_model(file: UploadFile = File(...)):
    """保存主舞台选中的 VRM，并把同源 URL 同步给 CEF 桌宠。"""
    if Path(file.filename or "").suffix.lower() != ".vrm":
        raise HTTPException(status_code=415, detail="仅支持 .vrm 模型文件")

    target = _current_model_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    total = 0
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="VRM 文件不能超过 100 MB")
                output.write(chunk)
        os.replace(temporary, target)
    finally:
        await file.close()
        temporary.unlink(missing_ok=True)

    model_url = f"/api/pet/model/current?v={uuid4().hex}"
    delivered = get_pet_service().drive({"type": "load_model", "url": model_url})
    return {"url": model_url, "size": total, "delivered": delivered}


@router.get("/model/current")
async def current_pet_model():
    target = _current_model_path()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="尚未上传自定义 VRM")
    return FileResponse(target, media_type="application/octet-stream", filename="current.vrm")
