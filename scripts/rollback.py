#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""撤销归档 — 按 manifest.json 反向移动，恢复到原始路径。

用法:
    rollback.py --manifest ~/Desktop/文件归档/manifest.json --dry-run
    rollback.py --manifest ~/Desktop/文件归档/manifest.json

说明:
    - 只对 mode=move 的记录做反向移动；copy 模式仅提示（原件仍在，无需撤销）
    - 原路径已被占用时跳过并报告，绝不覆盖
    - 反向移动成功后清理变空的分类文件夹
"""
import argparse
import json
import os
import shutil
import sys


def main():
    ap = argparse.ArgumentParser(description="撤销文件归档")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(os.path.expanduser(args.manifest), encoding="utf-8") as f:
        mf = json.load(f)

    entries = [e for e in mf.get("entries", []) if e.get("status") == "ok"]
    moves = [e for e in entries if e.get("mode") == "move"]
    copies = [e for e in entries if e.get("mode") == "copy"]

    print(f"模式: {mf.get('mode')}   记录 {len(entries)} 条（可撤销 {len(moves)} 条）")
    if copies:
        print(f"[提示] {len(copies)} 条为复制模式，原件仍在原位，无需撤销。")

    if not moves:
        print("没有需要撤销的移动记录。")
        return

    ok = skip = fail = 0
    occupied = []
    for e in moves:
        src, dst = e["src"], e["dst"]
        if not os.path.exists(dst):
            skip += 1
            continue
        if os.path.exists(src):
            skip += 1
            occupied.append(src)
            continue
        if args.dry_run:
            ok += 1
            continue
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            shutil.move(dst, src)
            ok += 1
        except Exception as ex:
            fail += 1
            print(f"[失败] {dst} -> {src}: {ex}")

    verbl = "将恢复" if args.dry_run else "已恢复"
    print(f"{verbl} {ok} 个文件，跳过 {skip} 个，失败 {fail} 个")
    if occupied:
        print(f"[注意] {len(occupied)} 个原路径已被占用（未覆盖），前 5 个：")
        for p in occupied[:5]:
            print(f"  {p}")

    if not args.dry_run:
        # 清理空的分类目录
        root = mf.get("dest_root", "")
        if root and os.path.isdir(root):
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                if dirpath == root:
                    continue
                try:
                    if not os.listdir(dirpath):
                        os.rmdir(dirpath)
                except OSError:
                    pass
        print("已完成反向恢复。")


if __name__ == "__main__":
    main()
