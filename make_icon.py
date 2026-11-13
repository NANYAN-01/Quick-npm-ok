#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app.ico / app.png（纯标准库，无需 Pillow）。

默认设计：深蓝圆角底板 + 青→蓝渐变的三根上升柱，表示「测速 + 排名」。
备用设计：仪表盘（--variant gauge）。

用法
----
    python make_icon.py                 # 生成默认图标（bars）
    python make_icon.py --variant gauge # 生成仪表盘版
    python make_icon.py --out-dir tmp   # 输出到指定目录

图标包含 16/20/24/32/40/48/64/128/256 九个尺寸：小尺寸用 BMP 存储，
128 以上用 PNG 存储，兼容 Windows 7 ~ 11 的任务栏、资源管理器和 exe 图标。
"""

import argparse
import math
import os
import struct
import zlib

BASE = 256.0                      # 设计稿坐标系
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

TX0, TY0, TX1, TY1, TR = 7.0, 7.0, 249.0, 249.0, 57.0
TILE_TOP = (10, 24, 47)
TILE_BOTTOM = (19, 47, 90)
WHITE = (255, 255, 255)
CYAN = (43, 224, 234)
MID = (43, 167, 245)
BLUE = (45, 104, 255)


# --------------------------------------------------------------------------- #
# 基础几何
# --------------------------------------------------------------------------- #

def lerp(a, b, t):
    return a + (b - a) * t


def mix(c0, c1, t):
    t = max(0.0, min(1.0, t))
    return (lerp(c0[0], c1[0], t), lerp(c0[1], c1[1], t), lerp(c0[2], c1[2], t))


def dist(x, y, cx, cy):
    return math.hypot(x - cx, y - cy)


def in_poly(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def tile_hit(x, y):
    if x < TX0 or x > TX1 or y < TY0 or y > TY1:
        return False
    cx = min(max(x, TX0 + TR), TX1 - TR)
    cy = min(max(y, TY0 + TR), TY1 - TR)
    return (x - cx) ** 2 + (y - cy) ** 2 <= TR * TR


def tile_color(x, y):
    return mix(TILE_TOP, TILE_BOTTOM, (y - TY0) / (TY1 - TY0))


# --------------------------------------------------------------------------- #
# 方案 bars：三根上升柱（默认）
# --------------------------------------------------------------------------- #

BARS = (
    (78.0, 46.0, CYAN),
    (128.0, 78.0, MID),
    (178.0, 110.0, BLUE),
)
BAR_WIDTH = 30.0
BAR_BASE = 192.0


def render_bars(x, y):
    if not tile_hit(x, y):
        return None

    radius = BAR_WIDTH / 2
    half = BAR_WIDTH / 2
    for cx, height, color in BARS:
        top = BAR_BASE - height
        if cx - half <= x <= cx + half and top <= y <= BAR_BASE:
            cy = min(max(y, top + radius), BAR_BASE - radius)
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                return color

    return tile_color(x, y)


# --------------------------------------------------------------------------- #
# 方案 gauge：仪表盘 + 指针
# --------------------------------------------------------------------------- #

GAUGE_CX, GAUGE_CY = 128.0, 132.0
GAUGE_OUT, GAUGE_IN = 94.0, 68.0
GAUGE_A0, GAUGE_A1 = 135.0, 405.0
NEEDLE_ANGLE = 312.0


def render_gauge(x, y):
    if not tile_hit(x, y):
        return None

    col = tile_color(x, y)
    thick = GAUGE_OUT - GAUGE_IN
    axis = (GAUGE_OUT + GAUGE_IN) / 2
    d = dist(x, y, GAUGE_CX, GAUGE_CY)

    angle = math.degrees(math.atan2(y - GAUGE_CY, x - GAUGE_CX))
    if angle < GAUGE_A0:
        angle += 360.0

    cap_r = thick / 2

    def cap_point(a):
        rad = math.radians(a)
        return (GAUGE_CX + axis * math.cos(rad), GAUGE_CY + axis * math.sin(rad))

    on_band = GAUGE_IN <= d <= GAUGE_OUT and GAUGE_A0 <= angle <= GAUGE_A1
    on_cap = dist(x, y, *cap_point(GAUGE_A0)) <= cap_r or dist(x, y, *cap_point(GAUGE_A1)) <= cap_r
    if on_band or on_cap:
        col = mix(CYAN, BLUE, (angle - GAUGE_A0) / (GAUGE_A1 - GAUGE_A0))

    rad = math.radians(NEEDLE_ANGLE)
    tip = (GAUGE_CX + math.cos(rad) * 64.0, GAUGE_CY + math.sin(rad) * 64.0)
    nx, ny = -math.sin(rad), math.cos(rad)
    poly = [
        (GAUGE_CX + nx * 7.0, GAUGE_CY + ny * 7.0),
        (tip[0] + nx * 3.0, tip[1] + ny * 3.0),
        (tip[0] - nx * 3.0, tip[1] - ny * 3.0),
        (GAUGE_CX - nx * 7.0, GAUGE_CY - ny * 7.0),
    ]
    if in_poly(x, y, poly) or dist(x, y, *tip) <= 3.0 or d <= 7.0:
        col = WHITE
    if d <= 13.0:
        col = WHITE
    if d <= 4.5:
        col = tile_color(x, y)
    return col


VARIANTS = {"bars": render_bars, "gauge": render_gauge}


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def render(variant, size):
    """按目标尺寸超采样渲染，返回 RGBA 字节。"""
    fn = VARIANTS[variant]
    ss = 4 if size >= 96 else 8
    step = 1.0 / ss
    scale = BASE / size
    samples = ss * ss
    out = bytearray(size * size * 4)

    for py in range(size):
        for px in range(size):
            rs = gs = bs = 0.0
            hits = 0
            for sy in range(ss):
                yy = (py + (sy + 0.5) * step) * scale
                for sx in range(ss):
                    xx = (px + (sx + 0.5) * step) * scale
                    color = fn(xx, yy)
                    if color is None:
                        continue
                    rs += color[0]
                    gs += color[1]
                    bs += color[2]
                    hits += 1
            if not hits:
                continue
            index = (py * size + px) * 4
            out[index] = round(rs / hits)
            out[index + 1] = round(gs / hits)
            out[index + 2] = round(bs / hits)
            out[index + 3] = round(hits * 255 / samples)
    return bytes(out)


def png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def to_png(pixels, size):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", ihdr)
            + png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + png_chunk(b"IEND", b""))


def to_dib(pixels, size):
    """32bpp BMP（ICO 内部格式，不含文件头），自下而上 BGRA + AND 掩码。"""
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0
    )
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            i = (y * size + x) * 4
            row += bytes((pixels[i + 2], pixels[i + 1], pixels[i], pixels[i + 3]))
        rows.append(bytes(row))
    xor = b"".join(rows)
    mask_stride = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_stride * size)
    return header + xor + mask


def build_ico(variant):
    images = []
    for size in SIZES:
        pixels = render(variant, size)
        data = to_png(pixels, size) if size >= 128 else to_dib(pixels, size)
        images.append((size, data))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0, 0, 1, 32, len(data), offset,
        )
        payload += data
        offset += len(data)
    return header + bytes(entries) + bytes(payload)


def main():
    parser = argparse.ArgumentParser(description="生成 app.ico / app.png")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="bars")
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    ico = build_ico(args.variant)
    ico_path = os.path.join(args.out_dir, "app.ico")
    png_path = os.path.join(args.out_dir, "app.png")

    with open(ico_path, "wb") as handle:
        handle.write(ico)
    with open(png_path, "wb") as handle:
        handle.write(to_png(render(args.variant, 256), 256))

    print(f"已生成 {ico_path} / {png_path}（{args.variant}，{len(ico) / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
