# -*- coding: utf-8 -*-
"""按窗口标题截图（含窗口阴影外的可见区域），用于文档与回归对比。

用法：python tools/shot.py "npm 源测速切换器" out.png [wait_ms]
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PIL import Image

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
gdi32 = ctypes.windll.gdi32

DWMWA_EXTENDED_FRAME_BOUNDS = 9


def find_window(keyword: str):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if keyword in buffer.value:
            found.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else (None, "")


def grab(hwnd, width, height, rect):
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    gdi32.SelectObject(hdc_mem, bitmap)

    # 先试 PrintWindow：即使窗口被其他窗口盖住也能抓到内容
    ok = user32.PrintWindow(wintypes.HWND(hwnd), hdc_mem, 2)  # PW_RENDERFULLCONTENT
    if not ok:
        gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, rect.left, rect.top, 0x00CC0020)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    return Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "npm 源测速切换器"
    out = sys.argv[2] if len(sys.argv) > 2 else "shot.png"
    wait = float(sys.argv[3]) / 1000 if len(sys.argv) > 3 else 0.5

    hwnd, title = find_window(keyword)
    if not hwnd:
        print(f"未找到标题包含「{keyword}」的窗口")
        return 1
    user32.SetForegroundWindow(wintypes.HWND(hwnd))
    time.sleep(wait)

    rect = wintypes.RECT()
    dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                 ctypes.byref(rect), ctypes.sizeof(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    image = grab(hwnd, width, height, rect)
    image.save(out)
    print(f"{title} -> {out} ({image.width}x{image.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
