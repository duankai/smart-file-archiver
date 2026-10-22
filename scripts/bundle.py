#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把检索工具打包进归档区，生成双击即用的启动脚本。

用法:
    bundle.py --dest ~/Desktop/文件归档

产出:
    <归档区>/_tool/serve.py
    <归档区>/_tool/assets/index_template.html
    <归档区>/启动文件搜索.command     （双击启动并打开浏览器）
"""
import argparse
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LAUNCHER = """#!/bin/bash
# 双击本文件即可启动文件检索工具。关闭终端窗口即停止服务。
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

PY=""
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3)"; do
  if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "未找到 python3，请先安装 Python 3。"
  read -r -p "按回车关闭…" _
  exit 1
fi

if [ ! -f "$DIR/index.json" ]; then
  echo "缺少 index.json，请先生成索引。"
  read -r -p "按回车关闭…" _
  exit 1
fi

echo "正在启动文件检索工具…"
"$PY" "$DIR/_tool/serve.py" --index "$DIR/index.json" --open-browser
"""


CLEANER = """#!/bin/bash
# 把「已确认在归档区有一份完整副本」的桌面源文件移入废纸篓。
# 微信等应用目录的源文件不会被触碰。可随时从废纸篓还原。
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

PY=""
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3)"; do
  if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "未找到 python3，请先安装 Python 3。"
  read -r -p "按回车关闭…" _
  exit 1
fi

"$PY" "$DIR/_tool/clean_source.py" --manifest "$DIR/manifest.json" \\
      --only-prefix "$HOME/Desktop"
echo
read -r -p "按回车关闭…" _
"""


def main():
    ap = argparse.ArgumentParser(description="打包检索工具到归档区")
    ap.add_argument("--dest", required=True, help="归档区根目录")
    ap.add_argument("--install-app", metavar="路径",
                    help="额外打包成 macOS .app（如 /Applications/文件搜索.app），"
                         "可加入 Dock / Launchpad / Spotlight，双击即用")
    args = ap.parse_args()

    dest = os.path.abspath(os.path.expanduser(args.dest))
    if not os.path.isdir(dest):
        sys.exit(f"[错误] 归档区不存在: {dest}")

    tool_dir = os.path.join(dest, "_tool")
    os.makedirs(os.path.join(tool_dir, "assets"), exist_ok=True)

    shutil.copy2(os.path.join(HERE, "serve.py"), os.path.join(tool_dir, "serve.py"))
    cleaner_src = os.path.join(HERE, "clean_source.py")
    if os.path.exists(cleaner_src):
        shutil.copy2(cleaner_src, os.path.join(tool_dir, "clean_source.py"))
    tmpl = os.path.join(ROOT, "assets", "index_template.html")
    if not os.path.exists(tmpl):
        sys.exit(f"[错误] 找不到界面模板: {tmpl}")
    shutil.copy2(tmpl, os.path.join(tool_dir, "assets", "index_template.html"))

    launcher = os.path.join(dest, "启动文件搜索.command")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(LAUNCHER)
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    cleaner = os.path.join(dest, "清理桌面已归档文件.command")
    with open(cleaner, "w", encoding="utf-8") as f:
        f.write(CLEANER)
    os.chmod(cleaner, os.stat(cleaner).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print("打包完成：")
    print(f"  {tool_dir}/serve.py")
    print(f"  {tool_dir}/clean_source.py")
    print(f"  {tool_dir}/assets/index_template.html")
    print(f"  {launcher}")
    print(f"  {cleaner}")
    print("\n双击「启动文件搜索.command」检索文件。")
    print("确认归档无误后，双击「清理桌面已归档文件.command」清理桌面源文件。")

    if args.install_app:
        app_target = os.path.abspath(os.path.expanduser(args.install_app))
        try:
            import make_app  # 同目录脚本
        except ImportError:
            sys.path.insert(0, HERE)
            import make_app
        import tempfile

        stage = tempfile.mkdtemp(prefix="filearchiver_icon_")
        make_app.build_app(app_target, stage)
        shutil.rmtree(stage, ignore_errors=True)
        print(f"\n已安装 macOS 应用: {app_target}")
        print("可在 Spotlight / Launchpad 搜「文件搜索」，或拖入 Dock 常驻。")


if __name__ == "__main__":
    main()
