"""Alibaba Cloud OSS helper used by the CosyVoice enrollment path."""

from __future__ import annotations

import os
import re
import uuid
from typing import Optional

import oss2

from config import (
    OSS_ACCESS_KEY_ID,
    OSS_ACCESS_KEY_SECRET,
    OSS_BUCKET_NAME,
    OSS_ENDPOINT,
    OSS_REF_AUDIO_DIR,
)


class OSSClient:
    def __init__(
        self,
        access_key_id: str | None = OSS_ACCESS_KEY_ID,
        access_key_secret: str | None = OSS_ACCESS_KEY_SECRET,
        endpoint: str = OSS_ENDPOINT,
        bucket_name: str = OSS_BUCKET_NAME,
        audio_dir: str = OSS_REF_AUDIO_DIR,
    ):
        self.access_key_id = (access_key_id or "").strip()
        self.access_key_secret = (access_key_secret or "").strip()
        self.endpoint = (endpoint or "").strip()
        self.bucket_name = (bucket_name or "").strip()
        self.audio_dir = (audio_dir or "").strip("/")
        self._validate()
        self.bucket = oss2.Bucket(
            oss2.Auth(self.access_key_id, self.access_key_secret),
            self.endpoint,
            self.bucket_name,
        )

    def upload_file(self, local_path: str, object_name: Optional[str] = None) -> str:
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"本地文件不存在: {local_path}")
        if object_name is None:
            extension = os.path.splitext(local_path)[1].lower() or ".wav"
            object_name = f"{uuid.uuid4().hex}{extension}"

        full_key = self._make_key(object_name)
        result = self.bucket.put_object_from_file(full_key, local_path)
        if result.status != 200:
            raise RuntimeError(f"OSS 上传失败，HTTP {result.status}")
        url = self._make_public_url(full_key)
        print(f"[OSS] 参考音频上传成功: {url}")
        return url

    def delete_file(self, object_name: str) -> bool:
        full_key = (
            object_name
            if self.audio_dir and object_name.startswith(f"{self.audio_dir}/")
            else self._make_key(object_name)
        )
        self.bucket.delete_object(full_key)
        print(f"[OSS] 已删除: oss://{self.bucket_name}/{full_key}")
        return True

    def get_object_name_from_url(self, url: str) -> Optional[str]:
        prefix = f"https://{self.bucket_name}.{self.endpoint}/"
        if url.startswith(prefix):
            return url[len(prefix):]
        match = re.search(r"/([^/?]+(?:/[^?]*)?)", url.split("://", 1)[-1])
        return match.group(1) if match else None

    def _make_key(self, object_name: str) -> str:
        clean_name = object_name.lstrip("/")
        return f"{self.audio_dir}/{clean_name}" if self.audio_dir else clean_name

    def _make_public_url(self, full_key: str) -> str:
        return f"https://{self.bucket_name}.{self.endpoint}/{full_key}"

    def _validate(self) -> None:
        required = {
            "OSS_ACCESS_KEY_ID": self.access_key_id,
            "OSS_ACCESS_KEY_SECRET": self.access_key_secret,
            "OSS_ENDPOINT": self.endpoint,
            "OSS_BUCKET_NAME": self.bucket_name,
        }
        missing = [
            key
            for key, value in required.items()
            if not value or value.upper().startswith("YOUR_")
        ]
        if missing:
            raise ValueError(f"CosyVoice 需要配置: {', '.join(missing)}")
