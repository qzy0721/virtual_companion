# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder specification for the desktop companion app."""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


PROJECT_ROOT = Path(SPECPATH).resolve().parent
FRONTEND_DIST = PROJECT_ROOT / "web" / "dist"
DEFAULT_AUDIO = PROJECT_ROOT / "reference_audio" / "default.wav"

if not FRONTEND_DIST.is_dir():
    raise SystemExit(
        f"未找到 {FRONTEND_DIST}；请先执行 npm run build "
        "或运行 scripts/build_package.ps1"
    )
if not DEFAULT_AUDIO.is_file():
    raise SystemExit("未找到 reference_audio/default.wav，无法生成默认角色")

# conda 布局：扩展模块（_ssl/pyexpat/_hashlib 等）的依赖 DLL 位于
# Library\bin。构建时若未激活 conda 环境，该目录不在 PATH 中，PyInstaller
# 的依赖分析（bindepend 在 Windows 上走默认搜索路径 + PATH）会漏收集，
# 冻结包报 "DLL load failed while importing _ssl/pyexpat"。构建期把
# Library\bin 加入 PATH，让依赖分析能找到全部运行时 DLL。
_conda_library_bin = Path(sys.base_prefix) / "Library" / "bin"
if _conda_library_bin.is_dir():
    os.environ["PATH"] = str(_conda_library_bin) + os.pathsep + os.environ.get("PATH", "")

# 双保险：_ssl 是 HTTPS 请求的命脉，显式收集其 OpenSSL 依赖，
# 不依赖上述 PATH 修复的扫描结果，保证 _ssl 一定可加载。
_openssl_binaries = []
_openssl_candidate_dirs = [
    Path(sys.base_prefix) / "Library" / "bin",
    Path(sys.base_prefix) / "DLLs",
    Path(sys.base_prefix),
]
for _dll_name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
    for _candidate_dir in _openssl_candidate_dirs:
        _dll = _candidate_dir / _dll_name
        if _dll.is_file():
            _openssl_binaries.append((str(_dll), "."))
            break
    else:
        raise SystemExit(
            f"未找到 OpenSSL 运行时 {_dll_name}（已搜索 {_openssl_candidate_dirs}），"
            "_ssl 将无法加载"
        )

# CEF 桌宠运行时包含 DLL、pak、locales 与辅助资源，不能只依赖模块扫描。
cef_datas, cef_binaries, cef_hiddenimports = collect_all("cef_capi")

datas = [
    (str(FRONTEND_DIST), "web/dist"),
    (str(PROJECT_ROOT / "prompts"), "prompts"),
    (str(DEFAULT_AUDIO), "reference_audio"),
    (str(PROJECT_ROOT / ".env.example"), "."),
] + cef_datas

hiddenimports = [
    "MemoryGenerate",
    "PromptOptimizer",
    "api",
    "fetch_baidu",
    "oss_client",
    "tts",
    "pet_window",
    "dashscope.audio.tts_v2",
    "h11",
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.on",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "websockets",
    "pydantic_core",
    "pydantic_core._pydantic_core",
] + cef_hiddenimports

a = Analysis(
    [str(PROJECT_ROOT / "app_launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=collect_dynamic_libs("curl_cffi") + _openssl_binaries + cef_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "IPython",
        "PIL",
        "cv2",
        "gradio",
        "librosa",
        "matplotlib",
        "numba",
        "numpy",
        "pandas",
        "pydub",
        "pynput",
        "scipy",
        "sklearn",
        "sounddevice",
        "soundfile",
        "sympy",
        "tensorflow",
        "torch",
        "torchaudio",
        "torchvision",
        "transformers",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VirtualCompanion.Server",
    icon=str(PROJECT_ROOT / "web" / "public" / "favicon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="VirtualCompanion.Server",
    upx=False,
)
