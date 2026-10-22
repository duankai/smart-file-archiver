#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把归档检索工具打包成 macOS .app（含自绘图标）。"""
import os
import shutil
import struct
import subprocess
import sys
import zlib

SS = 2  # 超采样倍数


def png_write(path, w, h, pixels):
    raw = b"".join(b"\x00" + bytes(pixels[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def rounded_box_dist(x, y, cx, cy, hw, hh, r):
    dx = abs(x - cx) - (hw - r)
    dy = abs(y - cy) - (hh - r)
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return (ax * ax + ay * ay) ** 0.5 + min(max(dx, dy), 0.0) - r


def circle_dist(x, y, cx, cy, r):
    return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 - r


def capsule_dist(x, y, x1, y1, x2, y2, r):
    vx, vy = x2 - x1, y2 - y1
    wx, wy = x - x1, y - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    return ((wx - t * vx) ** 2 + (wy - t * vy) ** 2) ** 0.5 - r


def mix(c1, c2, t):
    return tuple(c1[i] + (c2[i] - c1[i]) * t for i in range(3))


def make_icon(size=1024):
    W = size * SS
    buf = [0] * (W * W * 3)
    s = W / 1024.0

    top = (18, 176, 168)      # 青绿
    bottom = (14, 48, 88)     # 深蓝
    accent = (255, 209, 102)  # 暖黄

    # 放大镜几何
    ring_c = (452 * s, 452 * s)
    ring_r_out = 250 * s
    ring_r_in = 182 * s
    hx1, hy1 = 596 * s, 596 * s
    hx2, hy2 = 780 * s, 780 * s
    handle_r = 52 * s

    for py in range(W):
        y = py + 0.5
        row = py * W * 3
        bg_t = min(1.0, max(0.0, y / W))
        base = mix(top, bottom, bg_t ** 0.85)
        for px in range(W):
            x = px + 0.5
            # 底板
            d = rounded_box_dist(x, y, W / 2, W / 2, 462 * s, 462 * s, 210 * s)
            cov_bg = 0.0 if d > 1.0 else (1.0 if d < -1.0 else 0.5 - d)
            if cov_bg <= 0.001:
                continue
            col = base

            # 内圈微光：让底板有层次
            gd = circle_dist(x, y, W * 0.30, W * 0.22, 520 * s)
            if gd < 0:
                col = mix(col, (255, 255, 255), 0.10 * (1 - abs(gd) / (520 * s)) ** 2)

            # 放大镜白环
            rc = ((x - ring_c[0]) ** 2 + (y - ring_c[1]) ** 2) ** 0.5
            ring = 0.0
            if ring_r_in - 1.5 < rc < ring_r_out + 1.5:
                dmin = min(rc - ring_r_out, ring_r_in - rc)
                ring = 1.0 if dmin < -1.0 else max(0.0, 0.5 - dmin)

            # 手柄
            dh = capsule_dist(x, y, hx1, hy1, hx2, hy2, handle_r)
            if dh < 1.5:
                ring = max(ring, 1.0 if dh < -1.0 else max(0.0, 0.5 - dh))

            if ring > 0:
                col = mix(col, (255, 255, 255), ring * 0.97)

            # 环内三条文件条（暖黄）
            if rc < ring_r_in - 4:
                for i, (by, bw) in enumerate(((320, 300), (452, 250), (584, 190))):
                    dd = rounded_box_dist(x, y, 452 * s, by * s, (bw / 2) * s, 34 * s, 30 * s)
                    a = 1.0 if dd < -1.0 else (0.0 if dd > 1.0 else 0.5 - dd)
                    if a > 0:
                        col = mix(col, accent, a * (0.95 if i != 1 else 0.75))
                        break

            i3 = (py * W + px) * 3
            buf[i3] = int(col[0])
            buf[i3 + 1] = int(col[1])
            buf[i3 + 2] = int(col[2])

    # 下采样
    out = bytearray(size * size * 3)
    for y in range(size):
        for x in range(size):
            r = g = b = 0
            for dy in range(SS):
                for dx in range(SS):
                    i3 = ((y * SS + dy) * W + x * SS + dx) * 3
                    r += buf[i3]; g += buf[i3 + 1]; b += buf[i3 + 2]
            n = SS * SS
            o3 = (y * size + x) * 3
            out[o3] = min(255, r // n)
            out[o3 + 1] = min(255, g // n)
            out[o3 + 2] = min(255, b // n)
    return bytes(out)


def build_icns(dst_dir, png_base):
    iconset = os.path.join(dst_dir, "icon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    specs = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
             (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
             (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")]
    for px, name in specs:
        subprocess.run(["sips", "-z", str(px), str(px), png_base,
                        "--out", os.path.join(iconset, f"icon_{name}.png")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    icns = os.path.join(dst_dir, "icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(iconset, ignore_errors=True)
    return icns


LAUNCHER = r'''#!/bin/bash
# 文件归档检索工具启动器
set -u
ARCHIVE="${FILEARCHIVE_ROOT:-$HOME/Desktop/文件归档}"

fail() {
  /usr/bin/osascript -e "display alert \"文件搜索\" message \"$1\" as critical" >/dev/null 2>&1
  exit 1
}

[ -f "$ARCHIVE/index.json" ] || fail "找不到索引文件：$ARCHIVE/index.json\\n请先运行归档流程生成索引，或设置环境变量 FILEARCHIVE_ROOT 指向归档区。"

PY=""
for c in "$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3" \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$c" ]; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "未找到 Python 3 解释器。"

RES=$("$PY" - "$ARCHIVE" <<'PYEOF'
import json, os, socket, sys, urllib.request
target = os.path.realpath(sys.argv[1])
def is_mine(p):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/meta" % p, timeout=0.5) as r:
            d = json.load(r)
        root = d.get("archive_root") or ""
        return os.path.realpath(root) == target
    except Exception:
        return False
def free(p):
    s = socket.socket(); s.settimeout(0.3)
    ok = s.connect_ex(("127.0.0.1", p)) != 0
    s.close(); return ok
for p in range(8766, 8830):
    if is_mine(p):
        print("OPEN", p); raise SystemExit
for p in range(8766, 8830):
    if free(p):
        print("START", p); raise SystemExit
print("NONE 0")
PYEOF
)

MODE=$(echo "$RES" | awk '{print $1}')
PORT=$(echo "$RES" | awk '{print $2}')

if [ "$MODE" = "OPEN" ]; then
  /usr/bin/open "http://127.0.0.1:$PORT/"
  exit 0
fi
[ "$MODE" = "START" ] || fail "端口 8766-8829 均被占用，无法启动服务。"

mkdir -p "$ARCHIVE/_tool"
nohup "$PY" "$ARCHIVE/_tool/serve.py" --index "$ARCHIVE/index.json" --port "$PORT" \
  >> "$ARCHIVE/_tool/serve.log" 2>&1 &

for i in $(seq 1 60); do
  sleep 0.2
  if /usr/bin/curl -s -m 1 -o /dev/null "http://127.0.0.1:$PORT/api/meta"; then
    /usr/bin/open "http://127.0.0.1:$PORT/"
    exit 0
  fi
done
fail "服务启动超时，请查看 $ARCHIVE/_tool/serve.log"
'''

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>文件搜索</string>
    <key>CFBundleDisplayName</key><string>文件搜索</string>
    <key>CFBundleIdentifier</key><string>local.duankai.filesearch</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>icon</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def build_app(app_path, stage):
    if os.path.exists(app_path):
        shutil.rmtree(app_path)
    macos = os.path.join(app_path, "Contents", "MacOS")
    res = os.path.join(app_path, "Contents", "Resources")
    os.makedirs(macos)
    os.makedirs(res)

    with open(os.path.join(app_path, "Contents", "Info.plist"), "w", encoding="utf-8") as f:
        f.write(PLIST)

    lp = os.path.join(macos, "launcher")
    with open(lp, "w", encoding="utf-8") as f:
        f.write(LAUNCHER)
    os.chmod(lp, 0o755)

    png = os.path.join(stage, "icon_1024.png")
    png_write(png, 1024, 1024, make_icon(1024))
    icns = build_icns(stage, png)
    shutil.copy2(icns, os.path.join(res, "icon.icns"))
    return app_path


def main():
    stage = "/tmp/filearchiver_icon"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    target = sys.argv[1] if len(sys.argv) > 1 else "/Applications/文件搜索.app"
    build_app(target, stage)
    print("已生成:", target)


if __name__ == "__main__":
    main()
