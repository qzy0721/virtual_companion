# -*- coding: UTF-8 -*-
"""
桌宠窗口管理服务（融合自 AI 虚拟人助手项目的 launcher.py JsApi）。

在守护线程中运行 TransparentPetWindow（CEF 离屏渲染 + Win32 透明窗口），
主进程通过 execute_js 线程安全队列把表情/动作/口型/模型消息转发给桌宠页面。
"""

import os
import sys
import json
import threading
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# 桌宠窗口尺寸限制（与 launcher.py 保持一致）
_PET_MIN_W, _PET_MAX_W = 200, 800
_PET_MIN_H, _PET_MAX_H = 300, 1200
_PET_THEMES = {"wisteria", "hatsune", "sakura", "matcha", "starry"}
_PET_IDLE_ACTIONS = {"idle", "angry", "blush", "clapping", "goodbye", "jump", "look_around", "relax", "sad", "sleepy", "surprised"}


class PetService:
    """桌宠窗口生命周期与消息中转"""

    def __init__(self):
        self._pet_window = None       # TransparentPetWindow 实例
        self._pet_thread: Optional[threading.Thread] = None
        self._ready = False           # 桌宠页面（VRM 舞台）就绪
        self._visible = False         # Win32 桌宠窗口是否处于显示状态
        self._available = True        # CEF 在本进程是否可用
        self._latest_model_url = ""   # 最近一次模型 URL（pet ready 后补发）
        self._flushed_model_url = ""  # 已补发给桌宠的模型 URL（防止 ready->补发 死循环）
        self._capabilities = {"expressions": [], "actions": []}
        self._role_name = ""
        self._theme = "wisteria"
        self._width = 500
        self._height = 500
        self._idle_action = "idle"
        self._lock = threading.Lock()
        self._preferences_path = self._resolve_preferences_path()
        self._load_preferences()

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return (
            self._pet_window is not None
            and self._pet_thread is not None
            and self._pet_thread.is_alive()
        )

    @property
    def ready(self) -> bool:
        return self.alive and self._ready

    def status(self) -> dict:
        return {
            "alive": self.alive,
            "ready": self.ready,
            "visible": self.alive and self._visible,
            "available": self._available,
        }

    # ------------------------------------------------------------------
    # 窗口生命周期
    # ------------------------------------------------------------------

    def open_pet(self, url: str, width: int = None, height: int = None) -> bool:
        """创建或恢复桌宠悬浮窗口。成功（或已在运行）返回 True。"""
        if not self._available:
            return False

        width = self._width if width is None else width
        height = self._height if height is None else height

        with self._lock:
            self._ready = False

            # 窗口线程仍存活：仅恢复显示
            if self.alive:
                try:
                    self._pet_window.resize(width, height)
                    self._pet_window.show()
                    self._visible = True
                    print("[Pet] 窗口线程仍在运行, 恢复显示")
                    return True
                except Exception as e:
                    print(f"[WARNING] [Pet] 恢复显示失败: {e}")

            width = max(_PET_MIN_W, min(_PET_MAX_W, int(width)))
            height = max(_PET_MIN_H, min(_PET_MAX_H, int(height)))

            def _thread_func():
                window = None
                try:
                    from pet_window import TransparentPetWindow

                    window = TransparentPetWindow(
                        url=url,
                        width=width, height=height,
                        on_ready=self._on_cef_ready,
                        on_hide=self._on_pet_hidden,
                        on_close=lambda: self._on_pet_closed(window),
                    )
                    self._pet_window = window
                    self._visible = True
                    print(f"[Pet] CEF 窗口已创建: {url}")
                    window.show()  # 阻塞直到 _running=False
                except Exception as e:
                    print(f"[ERROR] [Pet] CEF 窗口异常: {e}")
                    import traceback; traceback.print_exc()
                    if self._pet_window is window:
                        self._pet_window = None
                    self._visible = False
                    self._available = False

            self._pet_thread = threading.Thread(target=_thread_func, daemon=True, name="pet-window")
            self._pet_thread.start()
            return True

    def close_pet(self) -> None:
        """隐藏桌宠窗口（不销毁，CEF 消息循环继续运行）"""
        self._ready = False
        self._visible = False
        if self._pet_window:
            try:
                self._pet_window.hide()
            except Exception:
                pass
        print("[Pet] 窗口已隐藏")

    def quit_pet(self) -> None:
        """退出桌宠界面，但保留 CEF 所属线程以支持之后稳定恢复。"""
        # CEF Browser 和 cef_do_message_loop_work 必须始终位于首次初始化 CEF
        # 的同一线程。这里不能销毁线程后另起线程重建，否则第二次 Browser
        # 创建会静默失败。真正销毁只在整个 Server 退出时进行。
        self.close_pet()
        print("[Pet] 桌宠已退出（CEF 引擎待命）")

    def _destroy_pet(self) -> None:
        """彻底销毁 Browser 与窗口线程，仅供 Server 关闭时调用。"""
        window = self._pet_window
        self._ready = False
        self._visible = False
        if window:
            try:
                window.close()
            except Exception:
                pass

        thread = self._pet_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        if self._pet_window is window:
            self._pet_window = None
        if self._pet_thread is thread and (not thread or not thread.is_alive()):
            self._pet_thread = None
        print("[Pet] CEF 桌宠引擎已销毁")

    def shutdown(self) -> None:
        """彻底销毁（进程退出前调用）"""
        self._destroy_pet()

    def resize(self, width: int, height: int) -> dict:
        """调整窗口尺寸（隐藏状态下也可调整，显示时自动生效）"""
        w = max(_PET_MIN_W, min(_PET_MAX_W, int(width)))
        h = max(_PET_MIN_H, min(_PET_MAX_H, int(height)))
        if self._pet_window:
            try:
                self._pet_window.resize(w, h)
            except Exception:
                pass
        print(f"[Pet] 窗口尺寸已调整为 {w}x{h}")
        return {"width": w, "height": h}

    def set_transparent(self, enabled: bool) -> None:
        """显式设置桌宠背景透明状态"""
        if not self.ready:
            return
        flag = "true" if enabled else "false"
        self._execute_js(f"if(window.setTransparent)window.setTransparent({flag});")
        print(f"[Pet] 透明背景: {'开启' if enabled else '关闭'}")

    # ------------------------------------------------------------------
    # 消息中转
    # ------------------------------------------------------------------

    def drive(self, data: dict) -> bool:
        """把驱动消息转发给桌宠页面（expression/volume/animation/reset/load_model）"""
        msg_type = data.get("type", "?")
        if msg_type == "load_model":
            # 记录最新模型：桌宠就绪前先缓存，就绪后补发
            url = data.get("url", "")
            self._latest_model_url = url
            print(f"[Pet] 模型同步: {url}")
        if not self.ready:
            return False
        payload = json.dumps(data, ensure_ascii=False)
        self._execute_js(f"if(window.receiveFromMain)window.receiveFromMain({payload});")
        if msg_type == "load_model":
            self._flushed_model_url = self._latest_model_url
        return True

    def mark_page_ready(self, capabilities: dict = None) -> None:
        """桌宠页面通知：VRM 舞台就绪（由 /api/pet/ready 调用）"""
        self._ready = True
        if capabilities:
            self._capabilities = {
                "expressions": list(capabilities.get("expressions") or []),
                "actions": list(capabilities.get("actions") or []),
            }
        print("[Pet] 页面就绪")
        # 补发最新模型（解决"先换模型再开桌宠"的时序问题）；
        # 已补发过的模型不重复发送，防止 ready->补发->ready 死循环
        if self._latest_model_url and self._latest_model_url != self._flushed_model_url:
            url = self._latest_model_url
            self._flushed_model_url = url
            payload = json.dumps({"type": "load_model", "url": url}, ensure_ascii=False)
            self._execute_js(f"if(window.receiveFromMain)window.receiveFromMain({payload});")
            print(f"[Pet] 补发模型同步: {url}")
        # ready 只会在模型和 VRMA 都加载完成后发出。此处再补发一次，既能覆盖
        # 设置发生在 CEF 初始化期间的情况，也能让换模型后的舞台恢复所选待机动作。
        idle_payload = json.dumps(
            {"type": "idle_action", "name": self._idle_action},
            ensure_ascii=False,
        )
        self._execute_js(
            f"if(window.receiveFromMain)window.receiveFromMain({idle_payload});"
        )
        print(f"[Pet] 应用待机动作: {self._idle_action}")

    def get_capabilities(self) -> dict:
        return self._capabilities

    def set_role(self, role_name: str) -> dict:
        """同步主窗口当前角色，供独立 CEF 页面发起对话。"""
        self._role_name = str(role_name or "").strip()
        return {"role_name": self._role_name, "theme": self._theme}

    def set_theme(self, theme: str) -> dict:
        """同步主窗口主题，并立即通知已加载的桌宠页面。"""
        normalized = str(theme or "").strip().lower()
        if normalized in _PET_THEMES:
            self._theme = normalized
            payload = json.dumps(normalized)
            self._execute_js(f"if(window.setPetTheme)window.setPetTheme({payload});")
        return {"theme": self._theme}

    def get_context(self) -> dict:
        return {"role_name": self._role_name, "theme": self._theme}

    def get_preferences(self) -> dict:
        return {
            "width": self._width,
            "height": self._height,
            "idle_action": self._idle_action,
        }

    def set_preferences(self, width: int, height: int, idle_action: str) -> dict:
        normalized_width = max(360, min(800, int(width)))
        normalized_height = max(360, min(800, int(height)))
        normalized_idle = str(idle_action or "idle").strip().lower()
        if normalized_idle not in _PET_IDLE_ACTIONS:
            normalized_idle = "idle"
        self._width = normalized_width
        self._height = normalized_height
        self._idle_action = normalized_idle
        self._save_preferences()
        self.resize(normalized_width, normalized_height)
        self.drive({"type": "idle_action", "name": normalized_idle})
        print(
            f"[Pet] 偏好已应用: width={normalized_width}, "
            f"height={normalized_height}, "
            f"idle_action={normalized_idle}, ready={self.ready}"
        )
        return self.get_preferences()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_preferences_path() -> Path:
        from config import USER_DATA_DIR

        return Path(USER_DATA_DIR) / "pet-preferences.json"

    def _load_preferences(self) -> None:
        try:
            data = json.loads(self._preferences_path.read_text(encoding="utf-8"))
            # 兼容上一版仅保存 size 的正方形配置；下一次保存时自动迁移为宽、高。
            legacy_size = int(data.get("size", 500))
            self._width = max(360, min(800, int(data.get("width", legacy_size))))
            self._height = max(360, min(800, int(data.get("height", legacy_size))))
            idle_action = str(data.get("idle_action", "idle")).strip().lower()
            self._idle_action = idle_action if idle_action in _PET_IDLE_ACTIONS else "idle"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._width = 500
            self._height = 500
            self._idle_action = "idle"

    def _save_preferences(self) -> None:
        self._preferences_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._preferences_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(self.get_preferences(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self._preferences_path)

    def _execute_js(self, js_code: str) -> None:
        if not self._pet_window:
            return
        try:
            self._pet_window.execute_js(js_code)
        except Exception as e:
            print(f"[WARNING] [Pet] JS 执行异常: {e}")

    def _on_cef_ready(self):
        """CEF 页面加载完成（注意：此时 VRM 模型可能尚未加载完）"""
        print("[Pet] CEF 页面加载完成")

    def _on_pet_hidden(self):
        """原生关闭或 API 隐藏都只改变可见状态，不销毁 CEF 线程。"""
        self._ready = False
        self._visible = False
        print("[Pet] 桌宠已隐藏，CEF 引擎继续待命")

    def _on_pet_closed(self, closed_window=None):
        """桌宠窗口彻底销毁（仅在进程退出时触发）"""
        print("[Pet] 窗口已彻底销毁")
        if closed_window is not None and self._pet_window is not closed_window:
            return
        self._pet_window = None
        self._pet_thread = None
        self._ready = False
        self._visible = False


# 单例
_pet_service: Optional[PetService] = None


def get_pet_service() -> PetService:
    """获取 PetService 单例"""
    global _pet_service
    if _pet_service is None:
        _pet_service = PetService()
    return _pet_service
