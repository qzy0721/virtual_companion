"""Phase 1 桌宠离线契约测试，不启动 CEF 或外部服务。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# 直接运行 `python tests/test_pet_phase1.py` 时，sys.path[0] 是 tests 目录，
# 需要在导入 backend 之前把其父目录（项目根目录）加入模块搜索路径。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.main import app
from backend.services.pet_service import PetService

class _AliveThread:
    def is_alive(self):
        return True

    def join(self, timeout=None):
        return None


class _FakePetWindow:
    def __init__(self):
        self.scripts = []
        self.hidden = False
        self.shown = False
        self.closed = False

    def execute_js(self, script):
        self.scripts.append(script)

    def hide(self):
        self.hidden = True

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True

    def resize(self, width, height):
        self.size = (width, height)


class PetPhase1Contracts(unittest.TestCase):
    def test_api_routes_are_registered(self):
        paths = app.openapi()["paths"]
        expected = {
            "/api/pet/open",
            "/api/pet/close",
            "/api/pet/quit",
            "/api/pet/status",
            "/api/pet/drive",
            "/api/pet/ready",
            "/api/pet/capabilities",
            "/api/pet/context",
            "/api/pet/theme",
            "/api/pet/preferences",
            "/api/pet/model",
            "/api/pet/model/current",
            "/api/shell/show-main",
            "/api/shell/next",
        }
        self.assertFalse(expected - set(paths))

    def test_ready_pet_receives_drive_messages(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()
        service.mark_page_ready({"expressions": ["happy"], "actions": ["wave"]})

        delivered = service.drive({"type": "expression", "name": "happy", "intensity": 0.8})

        self.assertTrue(delivered)
        self.assertIn("window.receiveFromMain", window.scripts[-1])
        self.assertEqual(service.get_capabilities()["expressions"], ["happy"])

    def test_hide_and_shutdown_delegate_to_window(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()

        service.close_pet()
        service.shutdown()

        self.assertTrue(window.hidden)
        self.assertTrue(window.closed)

    def test_quit_hides_pet_and_keeps_cef_thread_for_future_launches(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()

        service.quit_pet()

        self.assertTrue(window.hidden)
        self.assertFalse(window.closed)
        self.assertIs(service._pet_window, window)
        self.assertTrue(service.alive)
        self.assertFalse(service.status()["visible"])
        self.assertTrue(service.status()["available"])

        reopened = service.open_pet("http://127.0.0.1:8000/app/pet.html")

        self.assertTrue(reopened)
        self.assertTrue(window.shown)
        self.assertTrue(service.status()["visible"])

    def test_reopened_pet_uses_saved_width_and_height(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()
        service._width = 680
        service._height = 440

        service.open_pet("http://127.0.0.1:8000/app/pet.html")

        self.assertEqual(window.size, (680, 440))

    def test_stale_close_callback_does_not_clear_reopened_pet(self):
        service = PetService()
        old_window = _FakePetWindow()
        replacement = _FakePetWindow()
        service._pet_window = replacement
        service._pet_thread = _AliveThread()

        service._on_pet_closed(old_window)

        self.assertIs(service._pet_window, replacement)
        self.assertTrue(service.alive)

    def test_native_window_close_callback_keeps_engine_reopenable(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()
        service._ready = True
        service._visible = True

        service._on_pet_hidden()

        self.assertFalse(service.ready)
        self.assertFalse(service.status()["visible"])
        self.assertTrue(service.alive)
        self.assertIs(service._pet_window, window)

    def test_selected_role_is_shared_with_pet_page(self):
        service = PetService()
        self.assertEqual(service.set_role("胡桃")["role_name"], "胡桃")
        self.assertEqual(service.set_theme("sakura"), {"theme": "sakura"})
        self.assertEqual(service.get_context(), {"role_name": "胡桃", "theme": "sakura"})

    def test_all_five_themes_are_forwarded_to_the_pet_page(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()

        for theme in ("wisteria", "hatsune", "sakura", "matcha", "starry"):
            self.assertEqual(service.set_theme(theme), {"theme": theme})

        self.assertEqual(len(window.scripts), 5)
        self.assertIn('window.setPetTheme', window.scripts[-1])
        self.assertIn('starry', window.scripts[-1])

    def test_pet_preferences_resize_stage_and_forward_idle_action(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()
        service._ready = True
        with tempfile.TemporaryDirectory() as directory:
            service._preferences_path = Path(directory) / "pet-preferences.json"

            preferences = service.set_preferences(620, 420, "look_around")

            self.assertEqual(
                preferences,
                {"width": 620, "height": 420, "idle_action": "look_around"},
            )
            self.assertEqual(window.size, (620, 420))
            self.assertIn('"type": "idle_action"', window.scripts[-1])
            self.assertTrue(service._preferences_path.is_file())

    def test_legacy_square_pet_preference_is_migrated_in_memory(self):
        service = PetService()
        with tempfile.TemporaryDirectory() as directory:
            service._preferences_path = Path(directory) / "pet-preferences.json"
            service._preferences_path.write_text(
                '{"size": 440, "idle_action": "relax"}', encoding="utf-8"
            )

            service._load_preferences()

            self.assertEqual(
                service.get_preferences(),
                {"width": 440, "height": 440, "idle_action": "relax"},
            )

    def test_page_ready_reapplies_saved_idle_action(self):
        service = PetService()
        window = _FakePetWindow()
        service._pet_window = window
        service._pet_thread = _AliveThread()
        service._idle_action = "sleepy"

        service.mark_page_ready()

        self.assertTrue(service.ready)
        self.assertIn('"type": "idle_action"', window.scripts[-1])
        self.assertIn('"name": "sleepy"', window.scripts[-1])

    def test_uploaded_main_model_is_published_for_cef_pet(self):
        service = PetService()
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "current.vrm"
            with (
                patch("backend.routers.pet._current_model_path", return_value=model_path),
                patch("backend.routers.pet.get_pet_service", return_value=service),
            ):
                response = TestClient(app).post(
                    "/api/pet/model",
                    files={"file": ("avatar.vrm", b"test-vrm-content", "application/octet-stream")},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(model_path.read_bytes(), b"test-vrm-content")
            self.assertEqual(service._latest_model_url, response.json()["url"])


if __name__ == "__main__":
    unittest.main()
