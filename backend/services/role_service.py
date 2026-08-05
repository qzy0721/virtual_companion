# -*- coding: UTF-8 -*-
"""
角色业务逻辑：创建、查询、删除角色，并管理双引擎复刻音色。
"""

import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional, List, Dict

# 确保项目根目录在 path 中
from config import (
    DEFAULT_TTS_PROVIDER,
    REFERENCE_AUDIO_DIR,
    REFERENCE_AUDIO_PATH,
    SYSTEM_PROMPT_FILE,
    reference_audio_relative_path,
    resolve_reference_audio_path,
)
from oss_client import OSSClient
from PromptOptimizer import PromptOptimizer
from tts import CosyVoiceTTSClient, QwenTTSClient
from json_store import json_file_lock, write_json


VOICE_PROVIDERS = {"cosyvoice", "qwen_tts"}


class RoleService:
    """角色管理服务"""

    def __init__(self):
        self._qwen_tts: Optional[QwenTTSClient] = None
        self._cosyvoice_tts: Optional[CosyVoiceTTSClient] = None
        self._oss: Optional[OSSClient] = None

    @property
    def qwen_tts(self) -> QwenTTSClient:
        if self._qwen_tts is None:
            self._qwen_tts = QwenTTSClient()
        return self._qwen_tts

    @property
    def cosyvoice_tts(self) -> CosyVoiceTTSClient:
        if self._cosyvoice_tts is None:
            self._cosyvoice_tts = CosyVoiceTTSClient()
        return self._cosyvoice_tts

    @property
    def oss(self) -> OSSClient:
        if self._oss is None:
            self._oss = OSSClient()
        return self._oss

    # ------------------------------------------------------------------
    # 角色 CRUD
    # ------------------------------------------------------------------

    def list_roles(self) -> List[str]:
        """列出所有角色名称"""
        return PromptOptimizer.list_all_roles()

    def get_role(self, role_name: str) -> Optional[Dict]:
        """获取角色完整信息"""
        return PromptOptimizer.get_role_info(role_name)

    def create_role(
        self,
        role_name: str,
        audio_bytes: Optional[bytes] = None,
        audio_filename: Optional[str] = None,
        baike_query: Optional[str] = None,
        voice_provider: str = DEFAULT_TTS_PROVIDER,
    ) -> Dict:
        """
        创建新角色：生成提示词 + 创建所选引擎音色 + 持久化。

        Args:
            role_name:      角色名称
            audio_bytes:    参考音频字节（可选，不传则用默认音频）
            audio_filename: 音频文件名（用于保存本地文件）
            baike_query:    百度百科词条名或URL（可选）。
                            None = 不使用百科知识（默认）；
                            空字符串 = 用角色名作为百科词条搜索；
                            非空字符串 = 用指定词条名或URL爬取。

        Returns:
            角色完整信息 dict

        Raises:
            ValueError: 角色已存在或创建失败
        """
        # 1. 检查角色是否已存在
        existing = PromptOptimizer.get_role_info(role_name)
        if existing:
            raise ValueError(f"角色 '{role_name}' 已存在")

        # 2. 爬取百度百科内容（如果指定了 baike_query）
        baike_content = ""
        if baike_query is not None:
            query = baike_query.strip() or role_name
            print(f"[INFO] 爬取百度百科: query='{query}'")
            try:
                from fetch_baidu import BaikeCrawler, BaikeError
                crawler = BaikeCrawler()
                result = crawler.crawl(query)
                baike_content = result.content_text
                if baike_content:
                    print(f"[INFO] 百科爬取成功: {len(baike_content)} 字")
                else:
                    print(f"[WARNING] 百科页面无正文内容，将不使用百科知识")
            except BaikeError as e:
                print(f"[WARNING] 百科爬取失败: {e}，将不使用百科知识")
            except Exception as e:
                print(f"[WARNING] 百科爬取异常: {e}，将不使用百科知识")

        # 3. 生成系统提示词
        print(f"[INFO] 为角色 '{role_name}' 生成系统提示词{'（含百科知识）' if baike_content else ''}...")
        optimizer = PromptOptimizer(role_name)
        success, prompt_text = optimizer.generate_optimized_prompt(baike_content=baike_content)
        if not success:
            raise ValueError(f"提示词生成失败: {prompt_text}")

        # 4. 处理参考音频
        local_audio_path = self._save_audio_locally(role_name, audio_bytes, audio_filename)
        local_audio_path, stored_audio_path = self._prepare_reference_audio(role_name, local_audio_path)

        # 5. 按角色选择的引擎创建复刻音色
        try:
            voice_data = self._create_voice(voice_provider, local_audio_path, role_name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise ValueError(f"音色创建失败: {e}")

        # 6. 持久化
        optimizer.set_reference_audio_path(stored_audio_path)
        saved = optimizer.save_to_json(
            voice_id=voice_data["voice_id"],
            voice_provider=voice_data["voice_provider"],
            oss_url=voice_data.get("oss_url"),
            target_model=voice_data["target_model"],
        )
        if not saved:
            try:
                self._delete_voice_resources(voice_data)
            except Exception as cleanup_error:
                print(f"[QWEN TTS] 保存失败后的音色清理失败: {cleanup_error}")
            raise ValueError("角色保存失败")

        # 7. 返回角色信息
        return PromptOptimizer.get_role_info(role_name)

    def delete_role(self, role_name: str) -> bool:
        """
        删除角色：清理云端音色资源和本地数据。

        Args:
            role_name: 角色名称

        Returns:
            是否删除成功
        """
        role_info = PromptOptimizer.get_role_info(role_name)
        if not role_info:
            return False

        errors = []

        # 1. 按角色记录的引擎清理音色及其云端参考音频。
        if role_info.get("voice_id"):
            try:
                self._delete_voice_resources(role_info)
            except Exception as e:
                errors.append(f"删除云端音色资源失败: {e}")

        # 2. 从 roles.json 中移除
        roles_file_updated = False
        try:
            with json_file_lock(SYSTEM_PROMPT_FILE):
                all_data = PromptOptimizer.load_prompts()
                filtered = [
                    item for item in all_data
                    if not (isinstance(item, dict) and item.get("role_name") == role_name)
                ]
                write_json(SYSTEM_PROMPT_FILE, filtered)
                roles_file_updated = True
        except Exception as e:
            all_data = []
            filtered = []
            errors.append(f"更新角色文件失败: {e}")

        # 3. 仅删除不再被其他角色引用的托管参考音频。
        if roles_file_updated:
            try:
                self._remove_unreferenced_audio(role_info.get("reference_audio_path"), filtered)
            except OSError as e:
                errors.append(f"删除本地参考音频失败: {e}")

        if errors:
            print(f"[WARNING] 删除角色 '{role_name}' 时出现以下问题:")
            for err in errors:
                print(f"   - {err}")
            # 即使清理有问题，只要数据删了就返回 True
            return len(filtered) < len(all_data)

        print(f"[INFO] 角色 '{role_name}' 已删除")
        return True

    def register_role(
        self,
        role_name: str,
        system_prompt: str,
        local_audio_path: str,
        voice_provider: str = DEFAULT_TTS_PROVIDER,
    ) -> Dict:
        """
        用已有的提示词和音频注册角色（跳过 LLM 生成）。

        与 create_role 的区别：
          - 不调 LLM 生成提示词，直接使用传入的 system_prompt
          - 适用于启动时自动注册默认角色或用户手写提示词的场景

        Args:
            role_name:         角色名称
            system_prompt:     系统提示词（外部已生成）
            local_audio_path:  参考音频的本地路径

        Returns:
            角色完整信息 dict

        Raises:
            ValueError: 角色已存在或创建失败
        """
        # 1. 检查角色是否已存在
        existing = PromptOptimizer.get_role_info(role_name)
        if existing:
            raise ValueError(f"角色 '{role_name}' 已存在")

        # 2. 将任何外部路径托管到应用数据目录，再继续注册。
        local_audio_path, stored_audio_path = self._prepare_reference_audio(role_name, local_audio_path)

        # 3. 按选择的引擎创建音色
        try:
            voice_data = self._create_voice(voice_provider, local_audio_path, role_name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise ValueError(f"音色创建失败: {e}")

        # 4. 持久化
        import datetime

        new_data = {
            "role_name": role_name,
            "system_prompt": system_prompt.strip(),
            "reference_audio_path": stored_audio_path,
            "timestamp": datetime.datetime.now().isoformat(),
            **voice_data,
        }

        try:
            with json_file_lock(SYSTEM_PROMPT_FILE):
                all_data = PromptOptimizer.load_prompts()
                if any(
                    isinstance(item, dict) and item.get("role_name") == role_name
                    for item in all_data
                ):
                    raise ValueError(f"角色 '{role_name}' 已存在")
                all_data.append(new_data)
                write_json(SYSTEM_PROMPT_FILE, all_data)
        except (OSError, ValueError):
            try:
                self._delete_voice_resources(voice_data)
            except Exception as cleanup_error:
                print(f"[QWEN TTS] 保存失败后的音色清理失败: {cleanup_error}")
            raise

        print(f"[REGISTER] 角色 '{role_name}' 注册完成, voice_id={voice_data['voice_id']}")

        return PromptOptimizer.get_role_info(role_name)

    def enable_voice(
        self,
        role_name: str,
        voice_provider: str = DEFAULT_TTS_PROVIDER,
    ) -> Dict:
        """为已有角色补充指定引擎的复刻音色。"""
        role_info = PromptOptimizer.get_role_info(role_name)
        if not role_info:
            raise ValueError(f"角色 '{role_name}' 不存在")
        if role_info.get("voice_id"):
            return role_info

        local_audio_path, stored_audio_path = self._prepare_reference_audio(
            role_name,
            role_info.get("reference_audio_path") or REFERENCE_AUDIO_PATH,
        )
        if stored_audio_path != role_info.get("reference_audio_path"):
            if not self._update_reference_audio_path(role_name, stored_audio_path):
                raise ValueError("参考音频已托管，但更新角色信息失败")

        try:
            voice_data = self._create_voice(voice_provider, local_audio_path, role_name)
        except Exception as e:
            raise ValueError(f"音色创建失败: {e}") from e

        if not PromptOptimizer.update_role_voice(
            role_name,
            voice_id=voice_data["voice_id"],
            voice_provider=voice_data["voice_provider"],
            oss_url=voice_data.get("oss_url"),
            target_model=voice_data["target_model"],
        ):
            try:
                self._delete_voice_resources(voice_data)
            except Exception as cleanup_error:
                print(f"[VOICE] Cleanup after save failure failed: {cleanup_error}")
            raise ValueError("音色创建成功，但保存角色信息失败")

        print(f"[VOICE] 角色 '{role_name}' 音色已启用: {voice_data['voice_id']}")
        return PromptOptimizer.get_role_info(role_name)

    def get_voice_id(self, role_name: str) -> Optional[str]:
        """获取角色的音色 ID"""
        return PromptOptimizer.get_voice_id_by_role(role_name)

    def migrate_voice_records(self) -> int:
        """为旧角色补充 voice_provider，不删除任何已有音色信息。"""
        with json_file_lock(SYSTEM_PROMPT_FILE):
            roles = PromptOptimizer.load_prompts()
            changed = 0
            for role in roles:
                if not isinstance(role, dict):
                    continue

                voice_id = role.get("voice_id")
                if voice_id and not role.get("voice_provider"):
                    role["voice_provider"] = self.resolve_voice_provider(role)
                    changed += 1

            if not changed:
                return 0

            write_json(SYSTEM_PROMPT_FILE, roles)
        print(f"[TTS] Annotated provider for {changed} legacy role(s)")
        return changed

    def migrate_reference_audio_paths(self) -> int:
        """将旧角色的绝对路径迁移到应用托管目录和相对路径。"""
        with json_file_lock(SYSTEM_PROMPT_FILE):
            roles = PromptOptimizer.load_prompts()
            changed = 0
            for role in roles:
                if not isinstance(role, dict) or not role.get("role_name"):
                    continue
                try:
                    _, stored_audio_path = self._prepare_reference_audio(
                        role["role_name"],
                        role.get("reference_audio_path") or REFERENCE_AUDIO_PATH,
                    )
                except (OSError, ValueError) as error:
                    print(f"[AUDIO] 角色 '{role['role_name']}' 的参考音频迁移失败: {error}")
                    continue
                if role.get("reference_audio_path") != stored_audio_path:
                    role["reference_audio_path"] = stored_audio_path
                    changed += 1

            if not changed:
                return 0

            write_json(SYSTEM_PROMPT_FILE, roles)
        print(f"[AUDIO] Migrated {changed} role reference audio path(s)")
        return changed

    @staticmethod
    def resolve_voice_provider(role_info: Dict) -> str:
        provider = (role_info.get("voice_provider") or "").strip().lower()
        if provider in VOICE_PROVIDERS:
            return provider
        target_model = (role_info.get("target_model") or "").lower()
        if target_model.startswith("cosyvoice") or role_info.get("oss_url"):
            return "cosyvoice"
        if target_model.startswith("qwen"):
            return "qwen_tts"
        return DEFAULT_TTS_PROVIDER if DEFAULT_TTS_PROVIDER in VOICE_PROVIDERS else "cosyvoice"

    def get_tts_client(self, role_info: Dict):
        provider = self.resolve_voice_provider(role_info)
        target_model = (role_info.get("target_model") or "").strip()
        if provider == "cosyvoice":
            if target_model and target_model != self.cosyvoice_tts.target_model:
                return CosyVoiceTTSClient(target_model=target_model)
            return self.cosyvoice_tts
        if target_model and target_model != self.qwen_tts.target_model:
            return QwenTTSClient(target_model=target_model)
        return self.qwen_tts

    def _create_voice(
        self,
        voice_provider: str,
        local_audio_path: str,
        role_name: str,
    ) -> Dict:
        provider = (voice_provider or DEFAULT_TTS_PROVIDER).strip().lower()
        if provider not in VOICE_PROVIDERS:
            raise ValueError("voice_provider 必须是 cosyvoice 或 qwen_tts")

        if provider == "qwen_tts":
            print(f"[VOICE] 使用 Qwen TTS 创建复刻音色: {role_name}")
            voice_id = self.qwen_tts.create_voice(
                local_path=local_audio_path,
                role_name=role_name,
                language="zh",
            )
            return {
                "voice_id": voice_id,
                "voice_provider": provider,
                "target_model": self.qwen_tts.target_model,
            }

        print(f"[VOICE] 使用 CosyVoice 创建复刻音色: {role_name}")
        oss_url = self.oss.upload_file(local_audio_path)
        try:
            voice_id = self.cosyvoice_tts.create_voice(
                audio_url=oss_url,
                role_name=role_name,
                language="zh",
            )
        except Exception:
            self._delete_oss_url(oss_url)
            raise
        return {
            "voice_id": voice_id,
            "voice_provider": provider,
            "target_model": self.cosyvoice_tts.target_model,
            "oss_url": oss_url,
        }

    def _delete_voice_resources(self, voice_data: Dict) -> None:
        provider = self.resolve_voice_provider(voice_data)
        voice_id = voice_data.get("voice_id")
        errors = []
        if voice_id:
            try:
                self.get_tts_client(voice_data).delete_voice(voice_id)
            except Exception as error:
                errors.append(str(error))
        if provider == "cosyvoice" and voice_data.get("oss_url"):
            try:
                self._delete_oss_url(voice_data["oss_url"])
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("; ".join(errors))

    def _delete_oss_url(self, oss_url: str) -> None:
        object_name = self.oss.get_object_name_from_url(oss_url)
        if object_name:
            self.oss.delete_file(object_name)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_audio_stem(role_name: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", role_name).strip("_-")
        return stem or "role"

    def _prepare_reference_audio(self, role_name: str, source_path: str) -> tuple[str, str]:
        """确保参考音频由应用托管，并返回绝对路径与持久化相对路径。"""
        local_path = resolve_reference_audio_path(source_path)
        if not local_path:
            raise ValueError("参考音频路径无效")

        source = Path(local_path).expanduser()
        if not source.is_file():
            raise ValueError(f"参考音频不存在: {source}")

        audio_directory = Path(REFERENCE_AUDIO_DIR)
        audio_directory.mkdir(parents=True, exist_ok=True)
        source = source.resolve()
        try:
            source.relative_to(audio_directory.resolve())
            managed_path = source
        except ValueError:
            extension = source.suffix.lower() or ".wav"
            managed_path = audio_directory / (
                f"{self._safe_audio_stem(role_name)}-{uuid.uuid4().hex[:12]}{extension}"
            )
            shutil.copy2(source, managed_path)
            print(f"[AUDIO] 已复制参考音频到: {managed_path}")

        stored_path = reference_audio_relative_path(managed_path)
        if not stored_path:
            raise ValueError("参考音频不在应用托管目录中")
        return str(managed_path), stored_path

    @staticmethod
    def _update_reference_audio_path(role_name: str, stored_audio_path: str) -> bool:
        try:
            with json_file_lock(SYSTEM_PROMPT_FILE):
                roles = PromptOptimizer.load_prompts()
                for role in roles:
                    if isinstance(role, dict) and role.get("role_name") == role_name:
                        role["reference_audio_path"] = stored_audio_path
                        break
                else:
                    return False

                write_json(SYSTEM_PROMPT_FILE, roles)
            return True
        except OSError:
            return False

    @staticmethod
    def _remove_unreferenced_audio(stored_audio_path: str | None, remaining_roles: List[Dict]) -> None:
        local_path = resolve_reference_audio_path(stored_audio_path)
        if not local_path:
            return

        candidate = Path(local_path).resolve()
        audio_directory = Path(REFERENCE_AUDIO_DIR).resolve()
        default_audio = Path(REFERENCE_AUDIO_PATH).resolve()
        try:
            candidate.relative_to(audio_directory)
        except ValueError:
            return
        if candidate == default_audio:
            return

        for role in remaining_roles:
            if not isinstance(role, dict):
                continue
            other_path = resolve_reference_audio_path(role.get("reference_audio_path"))
            if other_path and Path(other_path).resolve() == candidate:
                return

        if candidate.is_file():
            candidate.unlink()
            print(f"[AUDIO] 已删除未引用的参考音频: {candidate}")

    def _save_audio_locally(
        self,
        role_name: str,
        audio_bytes: Optional[bytes],
        audio_filename: Optional[str],
    ) -> str:
        """
        保存参考音频到本地 reference_audio/ 目录。

        Returns:
            本地文件路径
        """
        os.makedirs(REFERENCE_AUDIO_DIR, exist_ok=True)

        if audio_bytes:
            extension = Path(audio_filename or "").suffix.lower() or ".wav"
            safe_name = f"{self._safe_audio_stem(role_name)}-{uuid.uuid4().hex[:12]}{extension}"
            filepath = os.path.join(REFERENCE_AUDIO_DIR, safe_name)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)
            print(f"[INFO] 参考音频已保存: {filepath}")
            return filepath

        # 无上传音频：尝试用默认音频
        if os.path.isfile(REFERENCE_AUDIO_PATH):
            print(f"[WARNING] 未上传参考音频，使用默认: {REFERENCE_AUDIO_PATH}")
            return REFERENCE_AUDIO_PATH

        raise ValueError("未提供参考音频，且默认音频文件不存在")


# 单例
_role_service: Optional[RoleService] = None


def get_role_service() -> RoleService:
    """获取 RoleService 单例"""
    global _role_service
    if _role_service is None:
        _role_service = RoleService()
    return _role_service
