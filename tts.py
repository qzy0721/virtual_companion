"""Qwen TTS HTTP client for voice cloning and speech synthesis."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi import requests

from config import (
    COSYVOICE_SAMPLE_RATE,
    COSYVOICE_TARGET_MODEL,
    COSYVOICE_VOICE_PREFIX,
    DASHSCOPE_API_KEY,
    DASHSCOPE_WORKSPACE_ID,
    QWEN_TTS_CUSTOMIZATION_URL,
    QWEN_TTS_SYNTHESIS_URL,
    QWEN_TTS_TARGET_MODEL,
    QWEN_TTS_VOICE_PREFIX,
)


_GENERIC_API_ROOT = "https://dashscope.aliyuncs.com/api/v1"
_CUSTOMIZATION_PATH = "/api/v1/services/audio/tts/customization"
_SYNTHESIS_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_SUPPORTED_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
}
_MAX_REFERENCE_AUDIO_BYTES = 10 * 1024 * 1024


class QwenTTSError(RuntimeError):
    """A readable error returned by the Qwen TTS service."""


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    extension: str
    content_type: str
    duration_ms: float = 0


class QwenTTSClient:
    """Thin HTTP client for Qwen voice enrollment and non-realtime TTS."""

    def __init__(
        self,
        api_key: str | None = DASHSCOPE_API_KEY,
        workspace_id: str = DASHSCOPE_WORKSPACE_ID,
        target_model: str = QWEN_TTS_TARGET_MODEL,
        voice_prefix: str = QWEN_TTS_VOICE_PREFIX,
        customization_url: str = QWEN_TTS_CUSTOMIZATION_URL,
        synthesis_url: str = QWEN_TTS_SYNTHESIS_URL,
    ):
        self.api_key = (api_key or "").strip()
        self.workspace_id = (workspace_id or "").strip()
        self.target_model = (target_model or "").strip()
        self.voice_prefix = (voice_prefix or "vc").strip()
        self.customization_url = (customization_url or "").strip() or self._default_url(
            _CUSTOMIZATION_PATH
        )
        self.synthesis_url = (synthesis_url or "").strip() or self._default_url(
            _SYNTHESIS_PATH
        )

    def create_voice(
        self,
        local_path: str,
        role_name: str,
        transcript: str | None = None,
        language: str = "zh",
    ) -> str:
        """Create a Qwen cloned voice directly from a local audio file."""
        self._require_configuration()
        audio_path = Path(local_path).expanduser()
        if not audio_path.is_file():
            raise FileNotFoundError(f"参考音频不存在: {audio_path}")

        suffix = audio_path.suffix.lower()
        mime_type = _SUPPORTED_AUDIO_TYPES.get(suffix)
        if not mime_type:
            raise ValueError("Qwen 声音复刻仅支持 WAV、MP3、M4A 或 MP4 音频")

        audio_bytes = audio_path.read_bytes()
        if not audio_bytes:
            raise ValueError("参考音频为空")
        if len(audio_bytes) > _MAX_REFERENCE_AUDIO_BYTES:
            raise ValueError("参考音频不能超过 10 MB")

        enrollment_input: dict[str, Any] = {
            "action": "create",
            "target_model": self.target_model,
            "preferred_name": self._preferred_name(role_name),
            "audio": {
                "data": (
                    f"data:{mime_type};base64,"
                    f"{base64.b64encode(audio_bytes).decode('ascii')}"
                )
            },
            "language": language,
        }
        if transcript and transcript.strip():
            enrollment_input["text"] = transcript.strip()

        payload = {
            "model": "qwen-voice-enrollment",
            "input": enrollment_input,
        }
        print(
            f"[QWEN TTS] 创建复刻音色: role={role_name}, "
            f"model={self.target_model}, audio={audio_path.name}"
        )
        response_data = self._post_json(self.customization_url, payload, timeout=120)
        output = response_data.get("output") or {}
        voice_id = output.get("voice")
        if not voice_id:
            raise QwenTTSError(self._api_error(response_data, "声音复刻未返回 voice"))

        if output.get("fallback_mode"):
            print(
                "[QWEN TTS] 音色以降级模式创建: "
                f"{output.get('fallback_reason') or 'unknown reason'}"
            )
        print(f"[QWEN TTS] 音色创建成功: {voice_id}")
        return str(voice_id)

    def delete_voice(self, voice_id: str) -> None:
        """Delete a cloned Qwen voice."""
        if not voice_id:
            return
        self._require_configuration()
        payload = {
            "model": "qwen-voice-enrollment",
            "input": {"action": "delete", "voice": voice_id},
        }
        self._post_json(self.customization_url, payload, timeout=60)
        print(f"[QWEN TTS] 音色已删除: {voice_id}")

    def list_voices(self, page_size: int = 100, page_index: int = 0) -> list[dict[str, Any]]:
        """List cloned Qwen voices for maintenance tools."""
        self._require_configuration()
        payload = {
            "model": "qwen-voice-enrollment",
            "input": {
                "action": "list",
                "page_size": page_size,
                "page_index": page_index,
            },
        }
        data = self._post_json(self.customization_url, payload, timeout=60)
        return list((data.get("output") or {}).get("voice_list") or [])

    def synthesize(self, text: str, voice_id: str) -> SynthesizedAudio | None:
        """Synthesize text and download the generated audio before its URL expires."""
        self._require_configuration()
        clean_text = self._clean_text(text)
        if not clean_text:
            return None
        if not voice_id:
            raise ValueError("缺少 Qwen voice_id")

        payload = {
            "model": self.target_model,
            "input": {
                "text": clean_text,
                "voice": voice_id,
                "language_type": "Auto",
            },
        }
        response_data = self._post_json(self.synthesis_url, payload, timeout=120)
        output = response_data.get("output") or {}
        audio_info = output.get("audio") or {}
        audio_url = audio_info.get("url")
        if not audio_url:
            raise QwenTTSError(self._api_error(response_data, "语音合成未返回音频地址"))

        try:
            audio_response = requests.get(audio_url, timeout=120)
            audio_response.raise_for_status()
        except Exception as error:
            raise QwenTTSError(f"下载 Qwen 合成音频失败: {error}") from error

        audio_bytes = audio_response.content
        if not audio_bytes:
            raise QwenTTSError("Qwen 合成音频为空")

        extension, content_type = self._detect_audio_format(
            audio_bytes,
            audio_response.headers.get("Content-Type", ""),
        )
        duration_ms = self._wav_duration_ms(audio_bytes) if extension == ".wav" else 0
        print(
            f"[QWEN TTS] 合成完成: {len(audio_bytes)} bytes, "
            f"text_len={len(clean_text)}, voice={voice_id}"
        )
        return SynthesizedAudio(
            data=audio_bytes,
            extension=extension,
            content_type=content_type,
            duration_ms=duration_ms,
        )

    def _default_url(self, path: str) -> str:
        if self.workspace_id:
            return f"https://{self.workspace_id}.cn-beijing.maas.aliyuncs.com{path}"
        if path == _CUSTOMIZATION_PATH:
            return f"{_GENERIC_API_ROOT}/services/audio/tts/customization"
        return f"{_GENERIC_API_ROOT}/services/aigc/multimodal-generation/generation"

    def _post_json(self, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
        except Exception as error:
            raise QwenTTSError(f"连接 Qwen TTS 服务失败: {error}") from error

        try:
            data = response.json()
        except ValueError as error:
            snippet = (response.text or "")[:300]
            raise QwenTTSError(
                f"Qwen TTS 返回了无法解析的响应 (HTTP {response.status_code}): {snippet}"
            ) from error

        if response.status_code >= 400 or data.get("code"):
            raise QwenTTSError(self._api_error(data, f"HTTP {response.status_code}"))
        return data

    def _require_configuration(self) -> None:
        if not self.api_key or self.api_key.upper().startswith("YOUR_"):
            raise QwenTTSError("未配置 DASHSCOPE_API_KEY")
        if not self.target_model:
            raise QwenTTSError("未配置 QWEN_TTS_TARGET_MODEL")

    def _preferred_name(self, role_name: str) -> str:
        role_part = self._ascii_name(role_name)
        prefix = self._ascii_name(self.voice_prefix)
        base = "_".join(part for part in (prefix, role_part) if part) or "vc"
        return f"{base[:10]}_{uuid.uuid4().hex[:5]}"[:16]

    @staticmethod
    def _ascii_name(value: str) -> str:
        ascii_part = re.sub(r"[^A-Za-z0-9_]+", "", value or "")
        if ascii_part:
            return ascii_part.lower()
        try:
            from pypinyin import lazy_pinyin

            pinyin = "".join(part[0] for part in lazy_pinyin(value or "") if part)
            pinyin = re.sub(r"[^A-Za-z0-9_]+", "", pinyin)
            if pinyin:
                return pinyin.lower()
        except ImportError:
            pass
        return hashlib.md5((value or "voice").encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _clean_text(text: str) -> str:
        clean = re.sub(r"\.{2,}|…+|⋯+", "", (text or "").strip())
        if not re.search(r"[\w\u4e00-\u9fff\u3040-\u30ff]", clean):
            return ""
        return clean

    @staticmethod
    def _detect_audio_format(audio: bytes, content_type: str) -> tuple[str, str]:
        if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
            return ".wav", "audio/wav"
        if audio.startswith(b"ID3") or audio[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            return ".mp3", "audio/mpeg"
        if len(audio) > 12 and audio[4:8] == b"ftyp":
            return ".m4a", "audio/mp4"
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        if normalized_type in {"audio/mpeg", "audio/mp3"}:
            return ".mp3", "audio/mpeg"
        if normalized_type in {"audio/mp4", "audio/x-m4a"}:
            return ".m4a", "audio/mp4"
        return ".wav", "audio/wav"

    @staticmethod
    def _wav_duration_ms(audio: bytes) -> float:
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                if frame_rate <= 0:
                    return 0
                return wav_file.getnframes() / frame_rate * 1000
        except (wave.Error, EOFError):
            return 0

    @staticmethod
    def _api_error(data: dict[str, Any], fallback: str) -> str:
        request_id = data.get("request_id")
        message = data.get("message") or data.get("code") or fallback
        return f"{message} (request_id={request_id})" if request_id else str(message)


class CosyVoiceTTSClient:
    """Original DashScope CosyVoice SDK path retained alongside Qwen TTS."""

    def __init__(
        self,
        api_key: str | None = DASHSCOPE_API_KEY,
        target_model: str = COSYVOICE_TARGET_MODEL,
        voice_prefix: str = COSYVOICE_VOICE_PREFIX,
    ):
        self.api_key = (api_key or "").strip()
        self.target_model = target_model
        self.voice_prefix = voice_prefix

    def create_voice(
        self,
        audio_url: str,
        role_name: str,
        language: str = "zh",
    ) -> str:
        self._configure_sdk()
        from dashscope.audio.tts_v2 import VoiceEnrollmentService

        voice_id = VoiceEnrollmentService().create_voice(
            target_model=self.target_model,
            prefix=self._sanitize_prefix(role_name),
            url=audio_url,
            language_hints=[language],
            max_prompt_audio_length=15.0,
        )
        if not voice_id:
            raise RuntimeError("CosyVoice 声音复刻未返回 voice_id")
        print(f"[COSYVOICE] 音色创建成功: {voice_id}")
        return voice_id

    def delete_voice(self, voice_id: str) -> None:
        if not voice_id:
            return
        self._configure_sdk()
        from dashscope.audio.tts_v2 import VoiceEnrollmentService

        VoiceEnrollmentService().delete_voice(voice_id)
        print(f"[COSYVOICE] 音色已删除: {voice_id}")

    def list_voices(self) -> list[dict[str, Any]]:
        self._configure_sdk()
        from dashscope.audio.tts_v2 import VoiceEnrollmentService

        response = VoiceEnrollmentService().list_voices()
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            return response.get("voices") or response.get("data", {}).get("voices") or []
        return []

    def synthesize(self, text: str, voice_id: str) -> SynthesizedAudio | None:
        clean_text = QwenTTSClient._clean_text(text)
        if not clean_text:
            return None
        self._configure_sdk()
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        synthesizer = SpeechSynthesizer(
            model=self.target_model,
            voice=voice_id,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        )
        try:
            pcm_bytes = synthesizer.call(clean_text)
        finally:
            try:
                synthesizer.close()
            except Exception:
                pass
        if not pcm_bytes:
            raise RuntimeError("CosyVoice 合成返回空数据")

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(COSYVOICE_SAMPLE_RATE)
            wav_file.writeframes(pcm_bytes)
        wav_bytes = buffer.getvalue()
        duration_ms = len(pcm_bytes) / 2 / COSYVOICE_SAMPLE_RATE * 1000
        return SynthesizedAudio(wav_bytes, ".wav", "audio/wav", duration_ms)

    def _configure_sdk(self) -> None:
        if not self.api_key or self.api_key.upper().startswith("YOUR_"):
            raise RuntimeError("未配置 DASHSCOPE_API_KEY")
        import dashscope

        dashscope.api_key = self.api_key

    @staticmethod
    def _sanitize_prefix(role_name: str) -> str:
        ascii_part = "".join(
            character
            for character in role_name
            if character.isascii() and character.isalnum()
        )
        if len(ascii_part) >= 2:
            return ascii_part[:10].lower()
        try:
            from pypinyin import lazy_pinyin

            initials = "".join(word[0] for word in lazy_pinyin(role_name) if word)
            initials = "".join(character for character in initials if character.isalnum())
            if initials:
                return initials[:10].lower()
        except ImportError:
            pass
        return f"v{hashlib.md5(role_name.encode()).hexdigest()[:8]}"
