"""
透明桌宠核心模块 -- CEF 离屏渲染 + Win32 WS_EX_LAYERED 透明窗口
================================================================
基于 Phase 0-4 验证结果构建的生产级模块。

特性:
  - CEF 131 离屏渲染 (OSR) + GPU 硬件加速 (D3D11)
  - Win32 WS_EX_LAYERED 逐像素 Alpha 透明窗口
  - OnPaint -> DIB Section -> UpdateLayeredWindow(ULW_ALPHA) 渲染管线
  - PetAPI JS->Python 抽象桥接层 (自动适配 pywebview / cefQuery)
  - WM_NCHITTEST 原生窗口拖拽 (无延迟)
  - Alpha 穿透: 透明区域自动鼠标穿透
  - 窗口生命周期管理 (创建/显示/隐藏/关闭/缩放/移动)

用法:
    from pet_window import TransparentPetWindow

    win = TransparentPetWindow(
        url='http://127.0.0.1:7864/avatar.html?pet=1',
        width=350, height=500,
        on_ready=lambda: print('Pet ready'),
        on_close=lambda: print('Pet closed'),
    )
    win.show()  # 阻塞直到窗口关闭
"""

import ctypes
from ctypes import wintypes
import time
import threading
import json
import queue
from typing import Callable, Optional

from cef_capi import (
    cef_string_ctor, size_ctor, base_ctor, handler,
    header, struct, decode_cef_string, cef_string_t,
    cef_pointer_to_struct
)
from cef_capi.app_client import client_ctor, app_ctor, settings_main_args_ctor

# ============================================================
# Win32 常量
# ============================================================
WS_EX_LAYERED     = 0x00080000
WS_EX_TOPMOST     = 0x00000008
WS_EX_TOOLWINDOW  = 0x00000080
WS_POPUP          = 0x80000000
ULW_ALPHA         = 0x00000002
WM_DESTROY        = 0x0002
WM_CLOSE          = 0x0010
WM_NCHITTEST      = 0x0084
WM_LBUTTONDOWN    = 0x0201
WM_LBUTTONUP      = 0x0202
WM_MOUSEMOVE      = 0x0200
WM_RBUTTONDOWN    = 0x0204
WM_RBUTTONUP      = 0x0205
WM_MBUTTONDOWN    = 0x0207
WM_MBUTTONUP      = 0x0208
WM_MOUSEWHEEL     = 0x020A
WM_MOUSEACTIVATE  = 0x0021
WM_SETFOCUS       = 0x0007
WM_KILLFOCUS      = 0x0008
MA_NOACTIVATE     = 3  # 不激活窗口但允许鼠标事件
MA_ACTIVATE       = 1  # 激活窗口
HTCAPTION         = 0x0002
HTCLIENT          = 0x0001
HTTRANSPARENT     = 0xFFFFFFFF  # 穿透到下层窗口
HTNOWHERE         = 0x0000
BI_RGB            = 0
DIB_RGB_COLORS    = 0
PM_REMOVE         = 1
DRAG_BAR_HEIGHT   = 28  # 顶部拖拽条高度 (与 .pet-drag-bar 一致)
DRAG_BAR_WIDTH    = 240  # 居中的原生拖动区，其余区域继续交给 CEF

# ============================================================
# 结构体
# ============================================================
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER)]

class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ('BlendOp', wintypes.BYTE), ('BlendFlags', wintypes.BYTE),
        ('SourceConstantAlpha', wintypes.BYTE), ('AlphaFormat', wintypes.BYTE),
    ]

class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]

class SIZE(ctypes.Structure):
    _fields_ = [('cx', wintypes.LONG), ('cy', wintypes.LONG)]

class MSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', wintypes.HWND), ('message', wintypes.UINT),
        ('wParam', wintypes.WPARAM), ('lParam', wintypes.LPARAM),
        ('time', wintypes.DWORD),
        ('pt_x', wintypes.LONG), ('pt_y', wintypes.LONG),
    ]

# Win32 API
user32 = ctypes.windll.user32
gdi32  = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = wintypes.LPARAM
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wintypes.BOOL

# 全局窗口类注册标志 & HWND->实例映射
_WINDOW_CLASS_REGISTERED = False
_windows = {}  # hwnd -> TransparentPetWindow


# 模块级静态 WNDPROC (避免实例方法绑定问题)
@WNDPROC
def _static_wnd_proc(hwnd, msg, wparam, lparam):
    instance = _windows.get(hwnd)
    if instance:
        return instance._handle_message(hwnd, msg, wparam, lparam)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ============================================================
# PetAPI 桥接 JS 代码
# ============================================================
PET_BRIDGE_JS = r"""
// PetAPI -- 抽象桥接层,适配 pywebview API(CEF 窗口通过 Python execute_js 通信)
(function() {
    window.PetAPI = {
        move: function(x, y) {
            // 桌宠模式下走 WM_NCHITTEST 原生拖拽,此方法为 pywebview 版保留
            if (window.pywebview && window.pywebview.api) {
                try { window.pywebview.api.move_pet(x, y); } catch(e) {}
            }
        },
        ready: function() {
            if (window.pywebview && window.pywebview.api) {
                try { window.pywebview.api.pet_ready(); } catch(e) {}
            }
        },
        close: function() {
            if (window.pywebview && window.pywebview.api) {
                try { window.pywebview.api.close_pet(); } catch(e) {}
            }
        },
        send: function(action, data) {
            if (window.pywebview && window.pywebview.api) {
                try { window.pywebview.api.send_to_pet(JSON.stringify({action:action, data:data})); } catch(e) {}
            }
        }
    };
    console.log('PetAPI bridge initialized');
})();
"""


def _create_permission_handler():
    """创建仅允许本地桌宠页访问麦克风的 CEF 权限处理器。"""
    permission_handler = base_ctor(struct.cef_permission_handler_t)

    # cef-capi-py 默认丢弃 C API 的第 0 个 self 参数，并自动解引用指针。
    # callback 必须保留为原始指针，才能调用其 cont/cancel 函数。
    @handler(permission_handler, raw_arg_indices={5})
    def on_request_media_access_permission(
        browser, frame, requesting_origin, requested_permissions, callback,
    ):
        origin = decode_cef_string(requesting_origin)
        audio_permission = header.CEF_MEDIA_PERMISSION_DEVICE_AUDIO_CAPTURE
        is_local = origin.startswith('http://127.0.0.1:') or origin.startswith('http://localhost:')
        if is_local and requested_permissions & audio_permission:
            callback.contents.cont(callback, audio_permission)
            print(f'[PetWindow] Microphone permission granted: {origin}')
        else:
            callback.contents.cancel(callback)
            print(f'[PetWindow] Media permission denied: {origin}')
        return 1

    return permission_handler


def _create_life_span_handler(pet_window):
    """创建桌宠 Browser 生命周期处理器，确保异步关闭完整走完。"""
    life_span_handler = base_ctor(struct.cef_life_span_handler_t)

    @handler(life_span_handler)
    def on_after_created(browser):
        pet_window._browser = browser
        host_ptr = browser.get_host(browser)
        pet_window._host_p = ctypes.cast(host_ptr, ctypes.POINTER(struct.cef_browser_host_t))
        if pet_window._closing:
            pet_window._host_p.contents.close_browser(pet_window._host_p, 1)

    @handler(life_span_handler)
    def on_before_close(browser):
        pet_window._browser = None
        pet_window._host_p = None
        pet_window._running = False
        print('[PetWindow] CEF browser closed')

    return life_span_handler


# ============================================================
# TransparentPetWindow
# ============================================================

# CEF 全局生命周期: 每个进程只能初始化/关闭一次
_cef_initialized = False

def _ensure_cef_initialized():
    """确保 CEF 已初始化 (仅首次调用时执行 cef_initialize)"""
    global _cef_initialized
    if _cef_initialized:
        return
    app = app_ctor()

    @handler(app)
    def on_before_command_line_processing(process_type, command_line):
        s_angle = cef_string_ctor('angle')
        s_d3d11 = cef_string_ctor('d3d11')
        for flag in ['enable-gpu', 'enable-gpu-compositing',
                     'ignore-gpu-blocklist', 'enable-unsafe-swiftshader']:
            command_line.append_switch(command_line, cef_string_ctor(flag))
        command_line.append_switch_with_value(command_line,
            cef_string_ctor('use-gl'), s_angle)
        command_line.append_switch_with_value(command_line,
            cef_string_ctor('use-angle'), s_d3d11)

    settings, main_args = settings_main_args_ctor()
    settings.log_severity = struct.LOGSEVERITY_WARNING
    settings.no_sandbox = 1
    settings.windowless_rendering_enabled = 1
    settings.command_line_args_disabled = 0
    header.cef_initialize(main_args, settings, app, None)
    _cef_initialized = True
    print('[PetWindow] CEF initialized (first time)')

def shutdown_cef():
    """关闭 CEF (进程退出时调用)"""
    global _cef_initialized
    if _cef_initialized:
        header.cef_shutdown()
        _cef_initialized = False
        print('[PetWindow] CEF shutdown complete')

# 注册进程退出时的 CEF 清理
import atexit as _atexit
_atexit.register(shutdown_cef)


class TransparentPetWindow:
    """CEF 离屏渲染 + Win32 透明窗口 桌宠"""

    def __init__(self, url: str, width: int = 500, height: int = 500,
                 on_ready: Optional[Callable] = None,
                 on_hide: Optional[Callable] = None,
                 on_close: Optional[Callable] = None):
        """
        Args:
            url: 要加载的 URL (通常 avatar.html?pet=1)
            width, height: 窗口初始尺寸
            on_ready: 页面加载完成回调
            on_hide: 用户关闭或请求隐藏窗口时的回调
            on_close: 窗口关闭回调
        """
        self.url = url
        self._width = width
        self._height = height
        self._on_ready = on_ready
        self._on_hide = on_hide
        self._on_close = on_close

        # Win32 资源
        self._hwnd = None
        self._mem_dc = None
        self._hbitmap = None
        self._pixel_ptr = None

        # CEF 资源
        self._browser = None
        self._client = None
        self._host_p = None       # 缓存 browser host 指针 (避免每次鼠标事件重复获取)

        # 状态
        self._running = False
        self._visible = True       # 窗口可见性
        self._is_destroyed = False # 是否已彻底销毁
        self._closing = False       # 已请求关闭，等待 CEF on_before_close
        self._close_deadline = 0.0  # CEF 异常时避免窗口线程永久卡住
        self._global_alpha = 255   # 全局透明度 (0-255, 用于淡入淡出)
        self._buffer = None        # bytes (latest BGRA)
        self._buffer_w = 0
        self._buffer_h = 0
        self._last_move_time = 0.0  # WM_MOUSEMOVE 节流
        self._pending_js = []        # 线程安全 JS 执行队列(由 pywebview 主线程写入,CEF 线程消费)
        self._js_lock = threading.Lock()
        self._commands = queue.Queue()
        self._owner_thread_id = None

    # ================================================================
    # Win32 窗口
    # ================================================================
    def _create_dib(self, width: int, height: int):
        """创建/重建 DIB Section,初始化为全透明"""
        if self._hbitmap:
            gdi32.DeleteObject(self._hbitmap)
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        screen_dc = user32.GetDC(None)
        pp = ctypes.c_void_p()
        hb = gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                     ctypes.byref(pp), None, 0)
        user32.ReleaseDC(None, screen_dc)
        if not hb:
            raise ctypes.WinError(ctypes.get_last_error())

        dc = gdi32.CreateCompatibleDC(None)
        gdi32.SelectObject(dc, hb)

        # 初始化为全透明 (避免显示未初始化的垃圾像素)
        ctypes.memset(pp, 0, width * height * 4)

        self._hbitmap = hb
        self._pixel_ptr = pp
        self._mem_dc = dc

    def _update_window(self):
        """将最新 BGRA 缓冲区绘制到透明窗口 (隐藏时跳过)"""
        if not self._visible or self._buffer is None or self._hwnd is None:
            return
        w, h = self._buffer_w, self._buffer_h
        if w == 0 or h == 0:
            return
        expected = w * h * 4
        if len(self._buffer) != expected:
            return

        ctypes.memmove(self._pixel_ptr, self._buffer, expected)

        blend = BLENDFUNCTION()
        blend.BlendOp = 0
        blend.BlendFlags = 0
        blend.SourceConstantAlpha = self._global_alpha
        blend.AlphaFormat = 1  # AC_SRC_ALPHA

        pt = POINT(0, 0)
        sz = SIZE(w, h)
        user32.UpdateLayeredWindow(
            self._hwnd, None, None, ctypes.byref(sz),
            self._mem_dc, ctypes.byref(pt),
            0, ctypes.byref(blend), ULW_ALPHA
        )

    def _handle_message(self, hwnd, msg, wparam, lparam):
        """实例级窗口消息处理 (由 _static_wnd_proc 转发)"""
        if msg == WM_CLOSE:
            # 任务管理器/系统窗口菜单等外部关闭请求只隐藏桌宠。CEF 必须保留
            # 在首次初始化线程，之后主界面才能可靠地再次弹出。
            self._hide_on_owner_thread()
            return 0

        if msg == WM_DESTROY:
            self._running = False
            user32.PostQuitMessage(0)
            return 0

        if msg == WM_NCHITTEST:
            screen_x = lparam & 0xFFFF
            screen_y = (lparam >> 16) & 0xFFFF
            pt = POINT(screen_x, screen_y)
            user32.ScreenToClient(hwnd, ctypes.byref(pt))

            drag_width = min(self._width, DRAG_BAR_WIDTH)
            drag_left = (self._width - drag_width) // 2
            if drag_left <= pt.x < drag_left + drag_width and 0 <= pt.y <= DRAG_BAR_HEIGHT:
                return HTCAPTION  # 拖拽条 -> 可拖动
            else:
                return HTCLIENT   # 角色区域 -> 鼠标消息转给 CEF

        # ---- 鼠标捕获: 旋转拖拽时鼠标可能离开窗口, SetCapture 确保持续接收事件 ----
        if msg == WM_LBUTTONDOWN:
            user32.SetCapture(hwnd)
            self._forward_mouse_to_cef(hwnd, msg, wparam, lparam)
            return 0
        if msg == WM_LBUTTONUP:
            user32.ReleaseCapture()
            self._forward_mouse_to_cef(hwnd, msg, wparam, lparam)
            return 0

        # ---- 其他鼠标消息转发到 CEF ----
        if msg in (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP,
                   WM_MOUSEMOVE, WM_MOUSEWHEEL):
            self._forward_mouse_to_cef(hwnd, msg, wparam, lparam)

        # ---- WM_MOUSEACTIVATE: 左键点击时激活(获取焦点以接收滚轮), 悬停时不激活 ----
        if msg == WM_MOUSEACTIVATE:
            # LOWORD(lparam) == HTCLIENT 表示鼠标在客户区
            hit_area = lparam & 0xFFFF
            if hit_area == HTCLIENT:
                # 区分点击和悬停: 检查是否按下鼠标键
                key_state = wparam & 0xFFFF
                if key_state & (0x0001 | 0x0002 | 0x0010):  # MK_LBUTTON | MK_RBUTTON | MK_MBUTTON
                    return MA_ACTIVATE   # 点击时激活以接收滚轮
                return MA_NOACTIVATE     # 悬停时不激活、不抢焦点
            return MA_NOACTIVATE

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _forward_mouse_to_cef(self, hwnd, msg, wparam, lparam):
        """将 Windows 鼠标消息转换为 CEF 事件并转发"""
        host_p = self._host_p
        if host_p is None:
            return

        # 解析鼠标坐标
        if msg == WM_MOUSEWHEEL:
            # WM_MOUSEWHEEL: lParam 是屏幕坐标 (不同于其他鼠标消息!)
            screen_x = lparam & 0xFFFF
            screen_y = (lparam >> 16) & 0xFFFF
            pt = POINT(screen_x, screen_y)
            user32.ScreenToClient(hwnd, ctypes.byref(pt))
            client_x = pt.x
            client_y = pt.y
        else:
            # 其他鼠标消息: lParam 已经是客户区坐标
            client_x = lparam & 0xFFFF
            client_y = (lparam >> 16) & 0xFFFF

        # WM_MOUSEMOVE 节流: ~60fps 足够流畅 (但 LButton down 时不节流, 确保拖拽流畅)
        if msg == WM_MOUSEMOVE:
            if not (wparam & 0x0001):  # 非拖拽状态才节流
                now = time.perf_counter()
                if now - self._last_move_time < 0.016:
                    return
                self._last_move_time = now

        # 构建 CEF 鼠标事件
        mouse_event = struct.cef_mouse_event_t()
        mouse_event.x = client_x
        mouse_event.y = client_y
        # 修饰键
        modifiers = 0
        if wparam & 0x0001: modifiers |= header.EVENTFLAG_LEFT_MOUSE_BUTTON
        if wparam & 0x0002: modifiers |= header.EVENTFLAG_RIGHT_MOUSE_BUTTON
        if wparam & 0x0004: modifiers |= header.EVENTFLAG_SHIFT_DOWN
        if wparam & 0x0008: modifiers |= header.EVENTFLAG_CONTROL_DOWN
        if wparam & 0x0010: modifiers |= header.EVENTFLAG_MIDDLE_MOUSE_BUTTON
        mouse_event.modifiers = modifiers

        if msg == WM_LBUTTONDOWN:
            host_p.contents.send_mouse_click_event(host_p, ctypes.byref(mouse_event),
                                         header.MBT_LEFT, False, 1)
        elif msg == WM_LBUTTONUP:
            host_p.contents.send_mouse_click_event(host_p, ctypes.byref(mouse_event),
                                         header.MBT_LEFT, True, 1)
        elif msg == WM_RBUTTONDOWN:
            host_p.contents.send_mouse_click_event(host_p, ctypes.byref(mouse_event),
                                         header.MBT_RIGHT, False, 1)
        elif msg == WM_RBUTTONUP:
            host_p.contents.send_mouse_click_event(host_p, ctypes.byref(mouse_event),
                                         header.MBT_RIGHT, True, 1)
        elif msg == WM_MBUTTONDOWN:
            host_p.contents.send_mouse_click_event(host_p, ctypes.byref(mouse_event),
                                         header.MBT_MIDDLE, False, 1)
        elif msg == WM_MBUTTONUP:
            host_p.contents.send_mouse_click_event(host_p, ctypes.byref(mouse_event),
                                         header.MBT_MIDDLE, True, 1)
        elif msg == WM_MOUSEMOVE:
            host_p.contents.send_mouse_move_event(host_p, ctypes.byref(mouse_event), False)
        elif msg == WM_MOUSEWHEEL:
            delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            host_p.contents.send_mouse_wheel_event(host_p, ctypes.byref(mouse_event), 0, delta)

    def _create_window(self):
        """创建 WS_EX_LAYERED 透明窗口"""
        global _WINDOW_CLASS_REGISTERED
        hinst = kernel32.GetModuleHandleW(None)
        class_name = 'PetWindowClass'

        if not _WINDOW_CLASS_REGISTERED:
            WNDCLASSEXW_FIELDS = [
                ('cbSize', wintypes.UINT), ('style', wintypes.UINT),
                ('lpfnWndProc', ctypes.c_void_p), ('cbClsExtra', wintypes.INT),
                ('cbWndExtra', wintypes.INT), ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HICON), ('hCursor', wintypes.HICON),
                ('hbrBackground', wintypes.HBRUSH), ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR), ('hIconSm', wintypes.HICON),
            ]
            WNDCLASSEXW = type('WNDCLASSEXW', (ctypes.Structure,), {'_fields_': WNDCLASSEXW_FIELDS})
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(_static_wnd_proc, ctypes.c_void_p)
            wc.cbClsExtra = 0; wc.cbWndExtra = 0
            wc.hInstance = hinst
            wc.hIcon = None; wc.hCursor = None; wc.hbrBackground = None
            wc.lpszMenuName = None; wc.lpszClassName = class_name; wc.hIconSm = None
            user32.RegisterClassExW(ctypes.byref(wc))
            _WINDOW_CLASS_REGISTERED = True

        # TOOLWINDOW 保持桌宠不出现在任务栏和 Alt-Tab；仍由托盘/主窗口控制。
        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        style = WS_POPUP

        self._hwnd = user32.CreateWindowExW(
            ex_style, class_name, 'Pet',
            style, 200, 200, self._width, self._height,
            None, None, hinst, None
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        # 注册 HWND->实例 映射,供 _static_wnd_proc 查找
        _windows[self._hwnd] = self

        # 立即显示初始透明 DIB (避免闪现白色)
        blend = BLENDFUNCTION()
        blend.BlendOp = 0; blend.BlendFlags = 0
        blend.SourceConstantAlpha = 255; blend.AlphaFormat = 1
        pt = POINT(0, 0); sz = SIZE(self._width, self._height)
        user32.UpdateLayeredWindow(
            self._hwnd, None, None, ctypes.byref(sz),
            self._mem_dc, ctypes.byref(pt),
            0, ctypes.byref(blend), ULW_ALPHA
        )

        user32.ShowWindow(self._hwnd, 1)

    # ================================================================
    # CEF 初始化
    # ================================================================
    def _init_cef(self):
        """初始化 CEF OSR 并加载 URL (CEF 全局初始化仅首次执行)"""
        _ensure_cef_initialized()  # 幂等: 仅首次执行 cef_initialize

        self._client = client_ctor()
        self_ = self  # 闭包引用

        # ---- Life Span Handler ----
        # close_browser 是异步操作；必须继续泵送 CEF 消息直到 on_before_close，
        # 否则旧 Browser 没有真正释放，下一次创建桌宠会失败。
        @handler(self._client)
        def get_life_span_handler(*_):
            return _create_life_span_handler(self_)

        # ---- Permission Handler ----
        # OSR 没有浏览器权限气泡；仅为本地桌宠页显式放行麦克风。
        @handler(self._client)
        def get_permission_handler(*_):
            return _create_permission_handler()

        # ---- Render Handler ----
        @handler(self._client)
        def get_render_handler(*_):
            rh = base_ctor(struct.cef_render_handler_t)

            @handler(rh)
            def on_paint(browser, element_type, dirty_rects_count,
                          dirty_rects, buffer, width, height):
                if element_type == header.PET_VIEW and width > 0:
                    self_._buffer = ctypes.string_at(buffer, width * height * 4)
                    self_._buffer_w = width
                    self_._buffer_h = height
                    # 首次绘制时诊断 Alpha
                    if self_._buffer_w > 0 and not hasattr(self_, '_alpha_reported'):
                        # 检查四角和中心的 alpha
                        data = self_._buffer
                        cx, cy = width // 2, height // 2
                        tl = data[2 * 4 + 3]  # top-left (2,2) alpha
                        tr = data[(width - 3) * 4 + 3]
                        bl = data[(height - 3) * width * 4 + 2 * 4 + 3]
                        br = data[((height - 3) * width + (width - 3)) * 4 + 3]
                        cc = data[(cy * width + cx) * 4 + 3]
                        print(f'[PetWindow] Alpha check: corners=[{tl},{tr},{bl},{br}] center={cc} size={width}x{height}')
                        self_._alpha_reported = True
                    self_._update_window()

            @handler(rh)
            def get_view_rect(browser, rect):
                rect.x = 0; rect.y = 0
                rect.width = self_._width; rect.height = self_._height
                return 1

            @handler(rh)
            def get_screen_info(browser, screen_info):
                screen_info.device_scale_factor = 1.0
                screen_info.depth = 32
                screen_info.depth_per_component = 8
                screen_info.is_monochrome = 0
                screen_info.rect.x = 0; screen_info.rect.y = 0
                screen_info.rect.width = self_._width
                screen_info.rect.height = self_._height
                screen_info.available_rect = screen_info.rect
                return 1

            return rh

        # ---- Load Handler ----
        @handler(self._client)
        def get_load_handler(*_):
            lh = base_ctor(struct.cef_load_handler_t)

            @handler(lh)
            def on_loading_state_change(browser, is_loading, can_go_back, can_go_forward):
                if not is_loading:
                    self_._browser = browser
                    # 缓存 browser host 指针 (避免每次鼠标事件重复获取)
                    host_ptr = browser.get_host(browser)
                    self_._host_p = ctypes.cast(host_ptr, ctypes.POINTER(struct.cef_browser_host_t))
                    # 设置 OSR 帧率为 60fps (默认可能只有 30, 导致卡顿感)
                    try:
                        self_._host_p.contents.set_windowless_frame_rate(self_._host_p, 60)
                        print('[PetWindow] OSR frame rate set to 60fps')
                    except Exception as e:
                        print(f'[PetWindow] set_windowless_frame_rate failed: {e}')
                    # 注入 PetAPI 桥接 (使用 cef_pointer_to_struct 处理指针)
                    try:
                        frame_ptr = browser.get_main_frame(browser)
                        frame = cef_pointer_to_struct(frame_ptr, struct.cef_frame_t)
                        js = cef_string_ctor(PET_BRIDGE_JS)
                        url_about = cef_string_ctor('about:blank')
                        frame.execute_java_script(frame, js, url_about, 0)
                        print('[PetWindow] PetAPI bridge injected')
                    except Exception as e:
                        print(f'[PetWindow] PetAPI injection skipped: {e}')
                    if self_._on_ready:
                        self_._on_ready()

            @handler(lh)
            def on_load_error(browser, frame, error_code, error_text, failed_url):
                if frame.is_main(frame):
                    print(f'[PetWindow] Load error: {decode_cef_string(error_text)}')

            return lh

        # ---- 创建浏览器 ----
        window_info = struct.cef_window_info_t()
        window_info.windowless_rendering_enabled = 1
        # CEF 131+ 支持在 window_info 上设置背景色
        if hasattr(window_info, 'background_color'):
            window_info.background_color = 0  # ARGB 全透明

        browser_settings = size_ctor(struct.cef_browser_settings_t)
        browser_settings.background_color = 0  # ARGB 全透明

        header.cef_browser_host_create_browser(
            window_info, self._client, cef_string_ctor(self.url),
            browser_settings, None, None
        )

    # ================================================================
    # 公共 API
    # ================================================================
    def show(self):
        """显示窗口 (首次调用阻塞直到窗口关闭, hide后再次show仅恢复可见性)"""
        if self._is_destroyed:
            print('[PetWindow] 窗口已销毁, 无法重新显示')
            return

        if self._running:
            # 窗口和 CEF 资源只能由创建它们的线程操作。
            self._commands.put(("show", None))
            return

        # 首次启动: 创建 Win32 窗口 + CEF 浏览器 + 进入消息循环
        self._owner_thread_id = threading.get_ident()
        # 提前标记运行，避免启动窗口内的第二次 open 重复初始化 CEF/Win32 资源。
        self._running = True
        self._create_dib(self._width, self._height)
        self._create_window()
        self._init_cef()

        # 淡入动画: 窗口从透明渐显
        self._global_alpha = 0
        self._animate_fade('in', 350)

        # 消息循环 (阻塞直到 _running = False)
        msg = MSG()

        while self._running:
            # Windows 消息
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == 0x0012:  # WM_QUIT
                    self._running = False
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            # CEF 消息处理
            header.cef_do_message_loop_work()

            if self._closing and self._close_deadline and time.monotonic() >= self._close_deadline:
                print('[PetWindow] CEF close timeout, forcing window loop exit')
                self._running = False

            # 处理来自 FastAPI 请求线程的窗口命令。
            while True:
                try:
                    command, payload = self._commands.get_nowait()
                except queue.Empty:
                    break
                if command == "show":
                    self._unhide()
                elif command == "hide":
                    self._hide_on_owner_thread()
                elif command == "resize":
                    self._resize_on_owner_thread(*payload)
                elif command == "close":
                    self._close_on_owner_thread()

            # 执行排队中的 JS(来自其他线程的 execute_js 调用)
            pending = None
            with self._js_lock:
                if self._pending_js:
                    pending = self._pending_js[:]
                    self._pending_js.clear()
            if pending:
                for js_code in pending:
                    try:
                        # 模型加载/重要消息打印日志
                        if 'load_model' in js_code or 'loadVRM' in js_code:
                            print(f"[PetWindow] 执行 JS: {js_code[:120]}...")
                        frame_ptr = self._browser.get_main_frame(self._browser)
                        frame = cef_pointer_to_struct(frame_ptr, struct.cef_frame_t)
                        js = cef_string_ctor(js_code)
                        url_about = cef_string_ctor('about:blank')
                        frame.execute_java_script(frame, js, url_about, 0)
                    except Exception as e:
                        print(f"[PetWindow] JS 执行异常: {e}")

            # 极短睡眠避免 CPU 100% 空转
            time.sleep(0.001)

        # 消息循环退出 -> 彻底清理
        self._cleanup()

    def hide(self):
        """线程安全地请求隐藏窗口（不销毁 CEF）。"""
        if self._running and not self._is_destroyed:
            self._commands.put(("hide", None))

    def _hide_on_owner_thread(self):
        if self._hwnd and self._visible:
            self._animate_fade('out', 200)  # 淡出 200ms
            user32.ShowWindow(self._hwnd, 0)
            self._visible = False
            print('[PetWindow] 窗口已隐藏')
            if self._on_hide:
                self._on_hide()

    def _animate_fade(self, direction, duration_ms):
        """逐帧淡入/淡出: 修改 SourceConstantAlpha 实现窗口透明度渐变
        Args:
            direction: 'in' (0->255) 或 'out' (255->0)
            duration_ms: 动画时长 (毫秒)
        """
        steps = 16
        delay = duration_ms / steps / 1000.0
        start = 0 if direction == 'in' else 255
        end = 255 if direction == 'in' else 0

        for i in range(steps + 1):
            t = i / steps
            # ease-in-out cubic
            eased = t < 0.5 and (4 * t * t * t) or (1 - (-2 * t + 2) ** 3 / 2)
            self._global_alpha = int(start + (end - start) * eased)
            if self._hwnd:
                self._update_window()
            time.sleep(delay)

    def _unhide(self):
        """重新显示窗口 (内部方法, 由 show() 调用)"""
        if self._hwnd and not self._visible:
            self._global_alpha = 255  # 恢复完全不透明
            user32.ShowWindow(self._hwnd, 1)  # SW_SHOW
            self._visible = True
            # 重新加载页面以确保渲染正确
            if self._browser:
                try:
                    frame_ptr = self._browser.get_main_frame(self._browser)
                    frame = cef_pointer_to_struct(frame_ptr, struct.cef_frame_t)
                    url = cef_string_ctor(self.url)
                    frame.load_url(frame, url)
                    print('[PetWindow] 窗口已恢复, 重新加载 URL')
                except Exception as e:
                    print(f'[PetWindow] URL reload error: {e}')
            print('[PetWindow] 窗口已恢复显示')

    def close(self):
        """线程安全地请求彻底销毁窗口和浏览器。"""
        if self._running and threading.get_ident() != self._owner_thread_id:
            self._commands.put(("close", None))
            return
        self._close_on_owner_thread()

    def _close_on_owner_thread(self):
        if self._is_destroyed:
            return
        print('[PetWindow] 正在彻底销毁...')
        self._is_destroyed = True
        self._closing = True
        self._close_deadline = time.monotonic() + 2.5
        self._visible = False
        if self._browser:
            try:
                host = self._browser.get_host(self._browser)
                host_p = ctypes.cast(host, ctypes.POINTER(struct.cef_browser_host_t))
                host_p.contents.close_browser(host_p, 1)
            except Exception:
                self._running = False
        elif self._client is None:
            # CEF 尚未开始创建 Browser，无需等待异步关闭回调。
            self._running = False

    def move(self, x: int, y: int):
        """移动窗口"""
        if self._hwnd:
            user32.SetWindowPos(self._hwnd, None, x, y, 0, 0,
                                0x0001 | 0x0004)  # NOSIZE | NOZORDER

    def resize(self, width: int, height: int):
        """线程安全地请求调整窗口大小。"""
        if self._running and threading.get_ident() != self._owner_thread_id:
            self._commands.put(("resize", (width, height)))
            return
        self._resize_on_owner_thread(width, height)

    def _resize_on_owner_thread(self, width: int, height: int):
        self._width = width
        self._height = height
        if self._hwnd:
            user32.SetWindowPos(self._hwnd, None, 0, 0, width, height,
                                0x0002 | 0x0004)  # NOMOVE | NOZORDER
            self._create_dib(width, height)
            # 通知 CEF 浏览器调整大小
            if self._browser:
                host = self._browser.get_host(self._browser)
                host_p = ctypes.cast(host, ctypes.POINTER(struct.cef_browser_host_t))
                host_p.contents.was_resized(host_p)

    def execute_js(self, js_code: str):
        """执行 JavaScript(线程安全：入队后在 CEF 消息循环中执行)"""
        if not self._browser or self._is_destroyed:
            return
        with self._js_lock:
            self._pending_js.append(js_code)

    def _cleanup(self):
        """清理 Win32/GDI 资源 (CEF 由 atexit 统一关闭)"""
        # CEF 不在此关闭 -- 由进程退出时的 atexit handler 统一调用 shutdown_cef()
        # Win32
        if self._hwnd:
            _windows.pop(self._hwnd, None)  # 移除映射
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._hbitmap:
            gdi32.DeleteObject(self._hbitmap)
            self._hbitmap = None
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        self._browser = None
        self._host_p = None
        self._client = None
        # 回调
        if self._on_close:
            self._on_close()
        self._owner_thread_id = None


# ============================================================
# 测试入口
# ============================================================
if __name__ == '__main__':
    import os
    from dotenv import load_dotenv

    load_dotenv()
    standalone_port = int(os.getenv('APP_PORT', '').strip() or '8000')
    standalone_url = f'http://127.0.0.1:{standalone_port}/app/pet.html?pet=1'
    print('[PetWindow] Starting standalone test...')
    print(f'[PetWindow] Loading: {standalone_url}')
    print('[PetWindow] The FastAPI server must already be running on this port.')
    win = TransparentPetWindow(
        url=standalone_url,
        width=450, height=650,
        on_ready=lambda: print('[PetWindow] Page ready!'),
        on_close=lambda: print('[PetWindow] Closed.'),
    )
    # 注意: show() 是阻塞的
    # 从其他线程调用 win.close() 可提前关闭
    print('[PetWindow] Use Ctrl+C to exit if window is not responding.')
    win.show()
