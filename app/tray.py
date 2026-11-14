# -*- coding: utf-8 -*-
"""系统托盘（纯 ctypes，不依赖第三方库）。

在独立线程里创建一个隐藏窗口，用 Shell_NotifyIcon 注册托盘图标；
点击事件通过队列转交给 Tk 主线程处理。
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


def _declare_prototypes():
    """显式声明原型：64 位下 LPARAM/HWND 不声明会被当成 int，导致溢出或句柄被截断。"""
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND

    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HICON

    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT

    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = ctypes.c_bool


class Tray:
    """托盘图标。事件通过 self.events 队列交给主线程。"""

    def __init__(self, icon_path: str, tooltip: str):
        self.icon_path = icon_path
        self.tooltip = tooltip[:120]
        self.events: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.failed = ""
        self._thread = None
        self._hwnd = None
        self._icon = None
        self._wndproc = None

    # ---------------- 生命周期 ----------------

    def start(self) -> bool:
        if self.failed:
            return False
        _declare_prototypes()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.ready.wait(3.0)
        return bool(self._hwnd)

    def stop(self):
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(wintypes.HWND(self._hwnd), WM_CLOSE, 0, 0)

    def pop_events(self) -> list:
        items = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    # ---------------- 内部 ----------------

    def _run(self):
        try:
            self._create_window()
        except Exception as exc:  # noqa: BLE001
            self.failed = str(exc)
            self.ready.set()
            return

        self.ready.set()
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._remove_icon()

    def _create_window(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        module = kernel32.GetModuleHandleW(None)

        self._wndproc = WNDPROC(self._on_message)

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = module
        wc.lpszClassName = "QuickNpmOkTray"
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = kernel32.GetLastError()
            if err != 1410:  # 已注册
                raise OSError(f"RegisterClassW failed: {err}")

        self._hwnd = user32.CreateWindowExW(
            0, "QuickNpmOkTray", "Quick-npm-ok tray", 0, 0, 0, 0, 0,
            None, None, module, None,
        )
        if not self._hwnd:
            raise OSError("CreateWindowExW failed")

        self._icon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self._icon:
            self._icon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION

        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = wintypes.HWND(self._hwnd)
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = wintypes.HICON(self._icon)
        data.szTip = self.tooltip
        if not ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError("Shell_NotifyIconW failed")

    def _remove_icon(self):
        try:
            data = NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            data.hWnd = wintypes.HWND(self._hwnd)
            data.uID = 1
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        except Exception:  # noqa: BLE001
            pass

    def _on_message(self, hwnd, msg, wparam, lparam):
        user32 = ctypes.windll.user32
        if msg == WM_TRAYICON:
            if lparam == WM_LBUTTONDBLCLK:
                self.events.put("show")
            elif lparam == WM_RBUTTONUP:
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                # 菜单交给 Tk 主线程画：系统菜单在本机主题下会渲染成空白框
                self.events.put(("menu", point.x, point.y))
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(wintypes.HWND(hwnd))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wintypes.HWND(hwnd), msg, wparam, lparam)

