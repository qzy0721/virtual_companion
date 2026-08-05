# -*- coding: UTF-8 -*-
"""
儿童陪伴智能助手 — 后端 API 服务

启动方式:
    # APP_PORT 在 .env 中留空时自动选择可用端口
    python -m backend.main

    或:
    uvicorn backend.main:app --host 0.0.0.0 --port 9000 --reload

API 文档:
    http://localhost:<实际端口>/docs  (Swagger UI)
"""

import os
import sys
import subprocess
import socket
from pathlib import Path

# 兼容直接执行 python backend/main.py；正常开发和打包均走模块入口，无需子模块各自修改 sys.path。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.runtime import is_frozen, schedule_frozen_restart


# ============================================================
# 启动初始化：清理 TTS 缓存 + 检查 roles.json
# ============================================================
import glob
import json
import datetime
from json_store import json_file_lock, read_json, write_json

def _init_startup():
    """启动时初始化：清理 TTS 缓存并写入可对话的本地默认角色。"""
    print("[INIT] initializing...")
    from config import (
        DASHSCOPE_API_KEY,
        DEFAULT_TTS_PROVIDER,
        DEFAULT_ROLE_NAME,
        DEFAULT_SYSTEM_PROMPT,
        OSS_ACCESS_KEY_ID,
        OSS_ACCESS_KEY_SECRET,
        REFERENCE_AUDIO_PATH,
        SYSTEM_PROMPT_FILE,
        TTS_TEMP_DIR,
        reference_audio_relative_path,
    )

    # 1. 清理 TTS 临时文件（启动时清空）
    tts_dir = TTS_TEMP_DIR
    if os.path.isdir(tts_dir):
        for pattern in ('tts_*.wav', 'tts_*.mp3', 'tts_*.m4a'):
            for f in glob.glob(os.path.join(tts_dir, pattern)):
                try:
                    os.remove(f)
                except OSError:
                    pass
        print(f"[INIT] TTS cache cleared: {tts_dir}")

    # 2. 首次启动时创建默认角色。对话提示词与音色注册解耦，避免依赖外部服务。
    roles_path = SYSTEM_PROMPT_FILE
    os.makedirs(os.path.dirname(roles_path), exist_ok=True)

    if not DEFAULT_ROLE_NAME or not DEFAULT_SYSTEM_PROMPT.strip():
        print("[INIT] 默认角色配置为空，跳过本地角色初始化")
        return

    with json_file_lock(roles_path):
        try:
            roles_data = read_json(roles_path)
            if not isinstance(roles_data, list):
                roles_data = [roles_data]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            roles_data = []

        default_role = next(
            (
                role
                for role in roles_data
                if isinstance(role, dict) and role.get("role_name") == DEFAULT_ROLE_NAME
            ),
            None,
        )
        if default_role is None:
            default_role = {
                "role_name": DEFAULT_ROLE_NAME,
                "system_prompt": DEFAULT_SYSTEM_PROMPT.strip(),
                "reference_audio_path": reference_audio_relative_path(REFERENCE_AUDIO_PATH) or REFERENCE_AUDIO_PATH,
                "timestamp": datetime.datetime.now().isoformat(),
                "voice_id": None,
                "voice_provider": DEFAULT_TTS_PROVIDER,
                "target_model": None,
            }
            roles_data.append(default_role)
            write_json(roles_path, roles_data)
            print(f"[INIT] Local default role '{DEFAULT_ROLE_NAME}' created")

    try:
        from backend.services.role_service import get_role_service

        service = get_role_service()
        service.migrate_reference_audio_paths()
        service.migrate_voice_records()
        default_role = service.get_role(DEFAULT_ROLE_NAME) or default_role
    except Exception as error:
        print(f"[INIT] Reference audio migration failed: {type(error).__name__}: {error}")

    if default_role.get("voice_id"):
        return

    required_credentials = {"DashScope API Key": DASHSCOPE_API_KEY}
    if DEFAULT_TTS_PROVIDER == "cosyvoice":
        required_credentials.update({
            "OSS Access Key ID": OSS_ACCESS_KEY_ID,
            "OSS Access Key Secret": OSS_ACCESS_KEY_SECRET,
        })
    missing_credentials = [
        name
        for name, value in required_credentials.items()
        if not (value or "").strip() or (value or "").strip().upper().startswith("YOUR_")
    ]
    if missing_credentials:
        print(f"[INIT] Default role voice pending: missing {', '.join(missing_credentials)}")
        return

    if not os.path.isfile(REFERENCE_AUDIO_PATH):
        print(f"[INIT] Default role voice pending: audio not found: {REFERENCE_AUDIO_PATH}")
        return

    try:
        from backend.services.role_service import get_role_service

        get_role_service().enable_voice(DEFAULT_ROLE_NAME)
        print(f"[INIT] Default role '{DEFAULT_ROLE_NAME}' voice registered")
    except Exception as error:
        print(f"[INIT] Default role voice registration failed: {type(error).__name__}: {error}")


from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.routers import roles, chat, asr, tts, history, memory, settings, pet, shell

# ============================================================
# 应用初始化
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理"""
    _init_startup()
    print('[INIT] initialization completed')
    yield
    # 桌宠拥有独立的 CEF/Win32 消息循环，服务退出前必须显式释放。
    from backend.services.pet_service import get_pet_service

    get_pet_service().shutdown()

app = FastAPI(
    lifespan=lifespan,
    title="儿童陪伴智能助手 API",
    description="""
## 功能

- **角色管理**: 创建/查询/删除角色，集成 Qwen 声音复刻
- **对话**: 流式 LLM 生成（SSE），TTS 语音合成分离
- **语音识别**: DashScope ASR 实时语音转文字
- **对话历史**: 持久化存储，按角色查询
- **长期记忆**: AI 自动提取，下次对话注入上下文
""",
    version="3.0.0",
)

# CORS 配置（允许前端跨域访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 路由注册
# ============================================================

app.include_router(roles.router)
app.include_router(chat.router)
app.include_router(asr.router)
app.include_router(tts.router)
app.include_router(history.router)
app.include_router(memory.router)
app.include_router(settings.router)
app.include_router(pet.router)
app.include_router(shell.router)

# 挂载静态目录（包裹 CORS 中间件，否则 app.mount 绕过 FastAPI CORS）
from config import RESOURCE_DIR, TTS_TEMP_DIR, USER_DATA_DIR
os.makedirs(TTS_TEMP_DIR, exist_ok=True)

_FAVICON_PATH = os.path.join(RESOURCE_DIR, "web", "public", "favicon.ico")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """兼容 Chromium 在解析页面图标声明前发起的根路径 favicon 请求。"""
    return FileResponse(_FAVICON_PATH, media_type="image/x-icon")

_tts_static = StaticFiles(directory=TTS_TEMP_DIR)
_tts_static = CORSMiddleware(_tts_static, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/tts_audio", _tts_static, name="tts_audio")

# Vue 3 + Vite 前端为默认入口。
_VUE_FRONTEND_DIR = os.path.join(RESOURCE_DIR, "web", "dist")
if os.path.isdir(_VUE_FRONTEND_DIR):
    _vue_frontend_static = StaticFiles(directory=_VUE_FRONTEND_DIR, html=True)
    _vue_frontend_static = CORSMiddleware(_vue_frontend_static, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.mount("/app", _vue_frontend_static, name="frontend-vue")

# ============================================================
# 健康检查
# ============================================================

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


# ============================================================
# 启动入口
# ============================================================

def _find_available_port(host: str = "127.0.0.1", start_port: int = 8000) -> int:
    """从 start_port 起查找一个可供本地开发服务器监听的端口。"""
    for port in range(start_port, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"未能在 {start_port}-65535 范围内找到可用端口")


def _resolve_server_port() -> int:
    """读取 .env 的 APP_PORT；留空时从 8000 起自动选择可用端口。"""
    configured_port = os.getenv("APP_PORT", "").strip()
    if not configured_port:
        return _find_available_port()

    try:
        port = int(configured_port)
    except ValueError as error:
        raise ValueError("APP_PORT 必须是 1-65535 之间的整数，或留空自动选择") from error

    if not 1 <= port <= 65535:
        raise ValueError("APP_PORT 必须是 1-65535 之间的整数，或留空自动选择")
    return port


def run_server(port: int | None = None) -> None:
    """启动服务；冻结态禁用 Uvicorn reload，由外层启动器负责平滑重启。"""
    import uvicorn
    from config import FZ_TEXT

    server_port = port or _resolve_server_port()
    app.state.server_port = server_port
    print(FZ_TEXT)
    print("=" * 17 + "  Backend Log  " + "=" * 18)
    print("Virtual Companion AI Assistant — Backend API v3.0.0")
    #print("=" * 50)
    print(f"RESOURCE DIR: {RESOURCE_DIR}")
    print(f"API Documentation: http://localhost:{server_port}/docs")
    print(f"HEALTH Check: http://localhost:{server_port}/api/health")
    print(f"Vue Frontend: http://localhost:{server_port}/app")
    print("=" * 50)

    if is_frozen():
        app.state.schedule_restart = schedule_frozen_restart
        # app_launcher 已在导入本模块前将 stdout/stderr 重定向到 server.log。
        # Uvicorn 也使用同一流，确保 print、traceback 与访问日志按时间顺序归档。
        frozen_log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "fmt": "%(levelprefix)s %(message)s",
                    "use_colors": False,
                },
                "access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": "%(levelprefix)s %(client_addr)s - '%(request_line)s' %(status_code)s",
                    "use_colors": False,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
                "access": {
                    "class": "logging.StreamHandler",
                    "formatter": "access",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            },
        }
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=server_port,
            reload=False,
            http="h11",
            ws="websockets",
            log_config=frozen_log_config,
        )
        return

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=server_port,
        reload=True,
        reload_dirs=[RESOURCE_DIR],
        reload_includes=[".env"],
        reload_delay=1.0,
    )


if __name__ == "__main__":
    run_server()
