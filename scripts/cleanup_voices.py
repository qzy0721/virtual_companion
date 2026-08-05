"""List or delete cloned voices from either supported TTS provider."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tts import CosyVoiceTTSClient, QwenTTSClient


def get_provider() -> str:
    for argument in sys.argv:
        if argument.startswith("--provider="):
            provider = argument.split("=", 1)[1].strip().lower()
            if provider in {"cosyvoice", "qwen_tts"}:
                return provider
    return "qwen_tts"


def get_client():
    return CosyVoiceTTSClient() if get_provider() == "cosyvoice" else QwenTTSClient()


def list_voices() -> list[dict]:
    provider = get_provider()
    voices = get_client().list_voices()
    if not voices:
        print(f"No {provider} voices found.")
        return []
    print(f"Found {len(voices)} {provider} voice(s):")
    for item in voices:
        voice_id = item.get("voice") or item.get("voice_id")
        print(
            f"  {voice_id or '?'}  "
            f"[{item.get('target_model', '?')}]  "
            f"{item.get('gmt_create', '')}"
        )
    return voices


def delete_voice(voice_id: str) -> bool:
    try:
        get_client().delete_voice(voice_id)
        return True
    except Exception as error:
        print(f"Failed to delete {voice_id}: {error}")
        return False


def main() -> None:
    if "--delete-all" in sys.argv:
        voices = list_voices()
        if not voices:
            return
        confirm = input(f"Delete all {len(voices)} Qwen voices? [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            return
        for item in voices:
            voice_id = item.get("voice") or item.get("voice_id")
            if voice_id:
                delete_voice(voice_id)
        print("Done.")
    elif "--delete" in sys.argv:
        index = sys.argv.index("--delete")
        if index + 1 >= len(sys.argv):
            print("Usage: python scripts/cleanup_voices.py --delete <voice_id>")
            return
        delete_voice(sys.argv[index + 1])
    else:
        list_voices()


if __name__ == "__main__":
    main()
