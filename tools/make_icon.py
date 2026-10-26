# -*- coding: utf-8 -*-
"""生成 AI 护照语音的醒目托盘图标(纯标准库, 无第三方依赖)。

设计: 亮橙圆角方块 (#FF8C42 品牌橙) 打底 + 白色麦克风 + 两侧白色声波弧,
在 Windows 白色任务栏上极醒目。输出多尺寸 ICO(16/24/32/48/64/128/256),
并直接由 PNG 数据拼装 ICO(PNG-in-ICO, Windows Vista+ 原生支持)。

用法: .venv/Scripts/python.exe tools/make_icon.py
输出: windows/passport-voice.ico (旧文件先备份为 .bak)
"""

import os
import math
import struct
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "windows", "passport-voice.ico")

# ---- 配色(品牌橙) ----
ORANGE_TOP = (255, 150, 78)
ORANGE_BOT = (233, 116, 40)
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def sd_round_rect(px, py, x0, y0, x1, y1, r):
    qx = abs(px - (x0 + x1) / 2) - ((x1 - x0) / 2 - r)
    qy = abs(py - (y0 + y1) / 2) - ((y1 - y0) / 2 - r)
    ax = max(qx, 0.0)
    ay = max(qy, 0.0)
    outside = math.hypot(ax, ay)
    inside = min(max(qx, qy), 0.0)
    return outside + inside - r


def sd_segment(px, py, ax, ay, bx, by, half):
    # 线段有向距离(胶囊)
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay) - half
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy) - half


def in_arc(px, py):
    dx = px - 0.5
    dy = py - 0.39
    r = math.hypot(dx, dy)
    ang = math.degrees(math.atan2(dy, dx))
    for R in (0.30, 0.40):
        if abs(r - R) < 0.035:
            # 左弧(约 180°)
            if 148 <= ang <= 212:
                return True
            # 右弧(约 0°, 处理 -180~180 环绕)
            if -32 <= ang <= 32:
                return True
    return False


def sample(px, py):
    """返回 (r,g,b,a) 0-255, 坐标归一化 0..1, y 向下。"""
    # 1) 圆角方块底(橙)
    d = sd_round_rect(px, py, 0.06, 0.06, 0.94, 0.94, 0.22)
    if d > 0:
        return (0, 0, 0, 0)
    # 垂直渐变
    t = max(0.0, min(1.0, (py - 0.06) / 0.88))
    col = lerp(ORANGE_TOP, ORANGE_BOT, t)

    # 2) 白色元素: 麦克风头(胶囊) + 杆 + 底座 + 声波弧
    mic_head = sd_segment(px, py, 0.5, 0.27, 0.5, 0.51, 0.115)  # 竖胶囊
    stem = sd_segment(px, py, 0.5, 0.50, 0.5, 0.66, 0.028)      # 细杆
    base = sd_round_rect(px, py, 0.35, 0.66, 0.65, 0.72, 0.03)  # 底座横条
    white_shape = min(mic_head, stem, base) < 0 or in_arc(px, py)
    if white_shape:
        return WHITE + (255,)
    return col + (255,)


def render_size(T, super_=4):
    S = T * super_
    buf = bytearray(T * T * 4)
    inv = 1.0 / S
    for oy in range(T):
        for ox in range(T):
            ar = ag = ab = aa = 0
            for sy in range(super_):
                py = (oy * super_ + sy + 0.5) * inv
                for sx in range(super_):
                    px = (ox * super_ + sx + 0.5) * inv
                    r, g, b, a = sample(px, py)
                    ar += r * a
                    ag += g * a
                    ab += b * a
                    aa += a
            n = super_ * super_
            aa /= n
            if aa > 0:
                # 预乘平均后还原颜色: sum(r*a)/sum(a), sum(a) = aa*n
                ar = ar / (aa * n)
                ag = ag / (aa * n)
                ab = ab / (aa * n)
            i = (oy * T + ox) * 4
            buf[i] = int(max(0, min(255, ar)))
            buf[i + 1] = int(max(0, min(255, ag)))
            buf[i + 2] = int(max(0, min(255, ab)))
            buf[i + 3] = int(round(aa))
    return bytes(buf)


def write_png(w, h, rgba):
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter none
        raw.extend(rgba[y * w * 4:(y + 1) * w * 4])
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = []
    for s in sizes:
        print(f"  渲染 {s}x{s} ...")
        rgba = render_size(s)
        pngs.append((s, write_png(s, s, rgba)))

    # 备份旧图标
    if os.path.isfile(OUT):
        bak = OUT + ".bak"
        if not os.path.isfile(bak):
            os.replace(OUT, bak)
            print(f"  旧图标已备份: {os.path.basename(bak)}")

    # 拼装 ICO (PNG-in-ICO)
    entries = bytearray()
    data = bytearray()
    offset = 6 + 16 * len(pngs)
    for s, png in pngs:
        entries += struct.pack("BBBBHHII",
                               s & 0xFF, s & 0xFF, 0, 0, 1, 32,
                               len(png), offset)
        data += png
        offset += len(png)
    icon = struct.pack("<HHH", 0, 1, len(pngs)) + entries + data
    with open(OUT, "wb") as f:
        f.write(icon)
    print(f"已生成: {OUT} ({os.path.getsize(OUT)} bytes, {len(pngs)} 尺寸)")


if __name__ == "__main__":
    main()
