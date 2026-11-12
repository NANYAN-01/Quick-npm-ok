#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app.ico（纯标准库，无需 Pillow）。

设计：蓝色圆角方块 + 白色闪电，表示"测速"。
"""

import struct
import zlib

SIZE = 256          # 输出尺寸
SS = 3              # 超采样倍数（抗锯齿）
N = SIZE * SS

BG_TOP = (26, 122, 255)
BG_BOTTOM = (10, 78, 200)
BOLT = [
    (150, 26), (84, 142), (120, 142), (104, 230), (178, 110), (138, 110),
]

# 圆角方块（在外层 256 坐标系里定义，再乘 SS）
X0, Y0, X1, Y1, R = 14, 14, 242, 242, 54


def in_round_rect(x, y):
    if x < X0 or x > X1 or y < Y0 or y > Y1:
        return False
    cx = min(max(x, X0 + R), X1 - R)
    cy = min(max(y, Y0 + R), Y1 - R)
    return (x - cx) ** 2 + (y - cy) ** 2 <= R * R


def in_polygon(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def sample(x, y):
    """返回 (r, g, b, a)"""
    if in_polygon(x, y, BOLT):
        return (255, 255, 255, 255)
    if in_round_rect(x, y):
        t = (y - Y0) / (Y1 - Y0)
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        return (r, g, b, 255)
    return (0, 0, 0, 0)


def build_pixels():
    out = bytearray(SIZE * SIZE * 4)
    step = 1.0 / SS
    for py in range(SIZE):
        for px in range(SIZE):
            sr = sg = sb = sa = 0
            for sy in range(SS):
                yy = (py + (sy + 0.5) * step) * SS
                for sx in range(SS):
                    xx = (px + (sx + 0.5) * step) * SS
                    r, g, b, a = sample(xx / SS, yy / SS)
                    sr += r * a
                    sg += g * a
                    sb += b * a
                    sa += a
            idx = (py * SIZE + px) * 4
            if sa == 0:
                out[idx:idx + 4] = b"\x00\x00\x00\x00"
            else:
                out[idx] = round(sr / sa)
                out[idx + 1] = round(sg / sa)
                out[idx + 2] = round(sb / sa)
                out[idx + 3] = round(sa / (SS * SS))
    return out


def png_chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(pixels):
    raw = bytearray()
    stride = SIZE * 4
    for y in range(SIZE):
        raw.append(0)  # filter: none
        raw += pixels[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + png_chunk(b"IEND", b"")
    )


def main():
    png = make_png(build_pixels())
    header = struct.pack("<HHH", 0, 1, 1)  # reserved, type=icon, count=1
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    with open("app.ico", "wb") as f:
        f.write(header + entry + png)
    with open("app.png", "wb") as f:
        f.write(png)
    print("已生成 app.ico / app.png")


if __name__ == "__main__":
    main()
