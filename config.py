# config.py
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

# PyInstaller one-folder 将只读资源置于 sys._MEIPASS；用户产生的数据不能写入其中。
_IS_FROZEN = bool(getattr(sys, "frozen", False))
_RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
if _IS_FROZEN:
    _data_root = os.getenv("APP_DATA_DIR", "").strip()
    if _data_root:
        _USER_DATA_DIR = Path(_data_root).expanduser().resolve()
    else:
        _USER_DATA_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "VirtualCompanion"
    _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    _USER_DATA_DIR = _RESOURCE_DIR

RESOURCE_DIR = str(_RESOURCE_DIR)
USER_DATA_DIR = str(_USER_DATA_DIR)
REFERENCE_AUDIO_RELATIVE_DIRECTORY = "reference_audio"


def _copy_missing_tree(source: Path, destination: Path) -> None:
    """首次运行时复制资源，后续只补充新文件，不覆盖用户修改。"""
    if not source.is_dir():
        return

    for source_path in source.rglob("*"):
        relative_path = source_path.relative_to(source)
        destination_path = destination / relative_path
        if source_path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=True)
        elif not destination_path.exists():
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)


def _initialize_editable_resources() -> None:
    """将首次运行所需的可编辑资源初始化到用户目录。"""
    if not _IS_FROZEN:
        return

    env_template = _RESOURCE_DIR / ".env.example"
    env_file = _USER_DATA_DIR / ".env"
    if env_template.is_file() and not env_file.exists():
        shutil.copy2(env_template, env_file)

    _copy_missing_tree(_RESOURCE_DIR / "prompts", _USER_DATA_DIR / "prompts")
    _copy_missing_tree(_RESOURCE_DIR / "reference_audio", _USER_DATA_DIR / "reference_audio")


_initialize_editable_resources()


def _resolve(relative_path: str) -> str:
    """解析安装包内或源码目录中的只读资源。"""
    return str(_RESOURCE_DIR / relative_path)


def _resolve_data(relative_path: str) -> str:
    """解析用户可写数据；开发时仍沿用项目目录。"""
    return str(_USER_DATA_DIR / relative_path)


def _resolve_editable_resource(relative_path: str) -> str:
    """解析可由用户维护的资源；开发态保持使用项目文件。"""
    base_dir = _USER_DATA_DIR if _IS_FROZEN else _RESOURCE_DIR
    return str(base_dir / relative_path)


def resolve_reference_audio_path(stored_path: str | None) -> str:
    """将 roles.json 中的新相对路径或旧绝对路径解析为本地路径。"""
    value = (stored_path or "").strip()
    if not value:
        return ""

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return str(candidate)

    user_root = _USER_DATA_DIR.resolve()
    resolved = (user_root / candidate).resolve()
    try:
        resolved.relative_to(user_root)
    except ValueError:
        return ""
    return str(resolved)


def reference_audio_relative_path(local_path: str | Path) -> str | None:
    """将托管目录内的音频转换为便于迁移的相对路径。"""
    try:
        audio_root = Path(REFERENCE_AUDIO_DIR).resolve()
        resolved = Path(local_path).expanduser().resolve()
        relative = resolved.relative_to(audio_root)
    except (OSError, ValueError):
        return None
    return (Path(REFERENCE_AUDIO_RELATIVE_DIRECTORY) / relative).as_posix()

# 必须在所有 os.getenv() 之前加载 .env
ENV_FILE = _resolve_data(".env")
# Uvicorn 的重载子进程会继承父进程环境。配置页更新 .env 后，需要让
# 新文件覆盖继承的旧值，才能确保重启后实际使用新配置。
load_dotenv(dotenv_path=ENV_FILE, override=True)

# 从文本文档获得提示词
def _get_prompt_from_text(file_path: str) -> str:
    '''从文本文档获得提示词'''
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()  
    except FileNotFoundError:
        print(f"[CONFIG ERR]. 请确保文件 {file_path} 存在")
        raise  
    except Exception as e:
        print(f"[CONFIG ERR]. {e}")
        raise

#提示词区
#默认的聊天系统提示词
DEFAULT_SYSTEM_PROMPT_PATH = _resolve_editable_resource('prompts/DEFAULT_SYSTEM_PROMPT.txt')
DEFAULT_SYSTEM_PROMPT = _get_prompt_from_text(DEFAULT_SYSTEM_PROMPT_PATH)

#AI生成记忆提示词
MEMORY_GENERATE_PROMPT_PATH = _resolve_editable_resource('prompts/MEMORY_GENERATE_PROMPT.txt')
MEMORY_GENERATE_PROMPT = _get_prompt_from_text(MEMORY_GENERATE_PROMPT_PATH)

#AI提示词优化器提示词
PROMPT_OPTIMIZER_PROMPT_PATH = _resolve_editable_resource('prompts/PROMPT_OPTIMIZER_PROMPT.txt')
PROMPT_OPTIMIZER_PROMPT = _get_prompt_from_text(PROMPT_OPTIMIZER_PROMPT_PATH)

#AI提示词优化器提示词（带百度百科知识补充）
PROMPT_OPTIMIZER_PROMPT_WITH_BAIKE_PATH = _resolve_editable_resource('prompts/PROMPT_OPTIMIZER_PROMPT_WITH_BAIKE.txt')
PROMPT_OPTIMIZER_PROMPT_WITH_BAIKE = _get_prompt_from_text(PROMPT_OPTIMIZER_PROMPT_WITH_BAIKE_PATH)

#表情分析提示词
EXPRESSION_ANALYSIS_PROMPT_PATH = _resolve_editable_resource('prompts/EXPRESSION_ANALYSIS_PROMPT.txt')
EXPRESSION_ANALYSIS_PROMPT = _get_prompt_from_text(EXPRESSION_ANALYSIS_PROMPT_PATH)

#佛祖
FZ_PATH = _resolve_editable_resource('prompts/FZ.txt')
try:
    with open(FZ_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
# 核心：把字面量反斜杠+n 替换成 两个反斜杠+n
# 这样 print 时，真正的换行符（\n ASCII码）依然换行，
# 而字面量 \n 会显示为 \n 文本
    FZ_TEXT = content.replace('\\n', '\\\\n')           
except Exception:
    FZ_TEXT = "" 

#配置区（从环境变量读取，带默认值）
#消息历史长度限制
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "10"))

#分句逻辑缓冲区最大大小
MAX_BUFFER_LENGTH = int(os.getenv("MAX_BUFFER_LENGTH", "200"))

#首次启动自动注册的默认角色名
DEFAULT_ROLE_NAME = os.getenv("DEFAULT_ROLE_NAME", "胡桃")

# 百度百科cookie
BAIDU_COOKIE = os.getenv("BAIDU_COOKIE", "")

# ----------------------------------------------------------
# 项目内部文件路径（使用相对路径，自动基于项目根目录解析）
# ----------------------------------------------------------

#对话历史记录文件位置
HISTORY_FILE = _resolve_data("backend/data/chat_history.json")

#历史记忆文件位置
HISTORY_MEMROY = _resolve_data("backend/data/memories.json")

#提示词文件位置
SYSTEM_PROMPT_FILE = _resolve_data("backend/data/roles.json")

#新的api接口
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
# ----------------------------------------------------------
# Qwen TTS 语音合成与声音复刻参数
# ----------------------------------------------------------

DEFAULT_TTS_PROVIDER = os.getenv("DEFAULT_TTS_PROVIDER", "cosyvoice").strip().lower()
QWEN_TTS_TARGET_MODEL = os.getenv("QWEN_TTS_TARGET_MODEL", "qwen3-tts-vc-2026-01-22")
QWEN_TTS_VOICE_PREFIX = os.getenv("QWEN_TTS_VOICE_PREFIX", "vc")
QWEN_TTS_CUSTOMIZATION_URL = os.getenv("QWEN_TTS_CUSTOMIZATION_URL", "")
QWEN_TTS_SYNTHESIS_URL = os.getenv("QWEN_TTS_SYNTHESIS_URL", "")

# CosyVoice 保留原有 SDK + OSS 声音复刻链路。
COSYVOICE_TARGET_MODEL = os.getenv("COSYVOICE_TARGET_MODEL", "cosyvoice-v3.5-plus")
COSYVOICE_SAMPLE_RATE = int(os.getenv("COSYVOICE_SAMPLE_RATE", "24000"))
COSYVOICE_VOICE_PREFIX = os.getenv("COSYVOICE_VOICE_PREFIX", "child_companion")

# TTS 临时音频文件目录（供 Web 端播放）
TTS_TEMP_DIR = _resolve_data("temp_tts")

# 本地参考音频存放目录
REFERENCE_AUDIO_DIR = _resolve_data("reference_audio")

# 默认参考音频路径（新建角色时若未上传音频，使用此默认文件）
# 注意：此文件应在 reference_audio 目录下
REFERENCE_AUDIO_PATH = _resolve_editable_resource("reference_audio/default.wav")

# 百炼 API 密钥（TTS / 音色复刻 / ASR 共用）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

# ----------------------------------------------------------
# 实时 ASR 参数（浏览器 PCM 流 -> 后端 WebSocket -> DashScope）
# ----------------------------------------------------------

# Paraformer 实时模型。流式桥接默认沿用原有模型，避免同时更换协议和模型。
ASR_REALTIME_MODEL = os.getenv("ASR_REALTIME_MODEL", "paraformer-realtime-v2")
ASR_REALTIME_SAMPLE_RATE = int(os.getenv("ASR_REALTIME_SAMPLE_RATE", "16000"))
ASR_VAD_SILENCE_MS = int(os.getenv("ASR_VAD_SILENCE_MS", "700"))
ASR_MAX_SESSION_SECONDS = int(os.getenv("ASR_MAX_SESSION_SECONDS", "120"))
ASR_MAX_FRAME_BYTES = int(os.getenv("ASR_MAX_FRAME_BYTES", "65536"))

# 优先使用业务空间专属域名；未配置时保持与既有 SDK 相同的兼容地址。
DASHSCOPE_WORKSPACE_ID = os.getenv("DASHSCOPE_WORKSPACE_ID", "")
DASHSCOPE_ASR_WS_URL = os.getenv("DASHSCOPE_ASR_WS_URL", "")

# 仅 CosyVoice 声音复刻需要 OSS。
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-shanghai.aliyuncs.com")
OSS_BUCKET_NAME = os.getenv("OSS_BUCKET_NAME", "cosyvoice-reference-voice")
OSS_REF_AUDIO_DIR = os.getenv("OSS_REF_AUDIO_DIR", "reference_audio")
