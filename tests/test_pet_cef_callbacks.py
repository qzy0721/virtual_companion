"""cef-capi-py 回调签名回归测试。"""

import ctypes
import sys
import unittest
from pathlib import Path

# 直接运行时把项目根目录加入模块搜索路径（cef-capi-py 未安装时自动跳过全部用例）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from cef_capi import base_ctor, cef_string_ctor, handler, header, struct
    from pet_window import _create_life_span_handler, _create_permission_handler
except ImportError:
    base_ctor = None


@unittest.skipIf(base_ctor is None, "cef-capi-py is not installed")
class CefPermissionCallbackContracts(unittest.TestCase):
    def test_local_microphone_request_is_granted_without_signature_error(self):
        granted = []
        cancelled = []
        callback = base_ctor(struct.cef_media_access_callback_t)

        @handler(callback)
        def cont(permissions):
            granted.append(permissions)

        @handler(callback)
        def cancel():
            cancelled.append(True)

        permission_handler = _create_permission_handler()
        origin = cef_string_ctor("http://127.0.0.1:9000")
        audio = header.CEF_MEDIA_PERMISSION_DEVICE_AUDIO_CAPTURE

        handled = permission_handler.on_request_media_access_permission(
            ctypes.byref(permission_handler),
            None,
            None,
            ctypes.byref(origin),
            audio,
            ctypes.byref(callback),
        )

        self.assertEqual(handled, 1)
        self.assertEqual(granted, [audio])
        self.assertFalse(cancelled)

    def test_browser_close_callback_completes_before_window_loop_exits(self):
        close_requests = []
        browser_host = base_ctor(struct.cef_browser_host_t)

        @handler(browser_host)
        def close_browser(force_close):
            close_requests.append(force_close)

        browser = base_ctor(struct.cef_browser_t)

        @handler(browser)
        def get_host():
            return browser_host

        class _WindowState:
            _browser = None
            _host_p = None
            _closing = True
            _running = True

        window = _WindowState()
        life_span = _create_life_span_handler(window)
        life_span.on_after_created(ctypes.byref(life_span), ctypes.byref(browser))

        self.assertEqual(close_requests, [1])
        self.assertTrue(window._running)

        life_span.on_before_close(ctypes.byref(life_span), ctypes.byref(browser))

        self.assertFalse(window._running)
        self.assertIsNone(window._browser)
        self.assertIsNone(window._host_p)


if __name__ == "__main__":
    unittest.main()
