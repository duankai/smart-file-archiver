#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4.5 — 清理已归档的源文件（仅在复制模式下需要）。

当归档因系统限制无法使用 move 模式时，文件会以 copy 方式进入归档区，
源文件仍在原位。本脚本负责把「已确认有一份完整副本」的源文件移入废纸篓。

用法:
    clean_source.py --manifest <归档区>/manifest.json --dry-run
    clean_source.py --manifest <归档区>/manifest.json --confirm

安全规则:
    - 只处理 manifest 中 status=ok 且目标文件确实存在的记录
    - 逐条校验目标文件大小与源文件一致，不一致跳过
    - 一律移入废纸篓（~/.Trash），不用 rm，随时可还原
    - 不加 --confirm 只做预演
    - 只处理指定前缀的源路径，防止误伤
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime


def main():
    ap = argparse.ArgumentParser(description="清理已归档的源文件")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--confirm", action="store_true", help="真正执行（否则只预演）")
    ap.add_argument("--dry-run", action="store_true", help="预演，列出将被清理的文件")
    ap.add_argument("--only-prefix", default="", help="只处理该路径前缀下的源文件")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    with open(os.path.expanduser(args.manifest), encoding="utf-8") as f:
        mf = json.load(f)

    entries = [e for e in mf.get("entries", []) if e.get("status") == "ok"]
    if args.only_prefix:
        entries = [e for e in entries if e["src"].startswith(os.path.expanduser(args.only_prefix))]
    if args.limit:
        entries = entries[: args.limit]

    trash = os.path.expanduser("~/.Trash")
    os.makedirs(trash, exist_ok=True)

    todo, skipped = [], []
    for e in entries:
        src, dst = e["src"], e["dst"]
        if not os.path.exists(src):
            skipped.append((src, "源文件已不存在（可能已移动）"))
            continue
        if not os.path.exists(dst):
            skipped.append((src, "归档副本不存在，拒绝清理"))
            continue
        try:
            if os.path.getsize(src) != os.path.getsize(dst):
                skipped.append((src, "大小与副本不一致，拒绝清理"))
                continue
        except OSError as ex:
            skipped.append((src, f"无法读取: {ex}"))
            continue
        todo.append(e)

    total_size = 0
    for e in todo:
        try:
            total_size += os.path.getsize(e["src"])
        except OSError:
            pass
    print(f"清单记录 {len(entries)} 条")
    print(f"可清理 {len(todo)} 个（{total_size/1024/1024/1024:.1f}GB）")
    print(f"跳过 {len(skipped)} 个")

    if skipped:
        print("\n跳过明细（前 10 条）：")
        for s, r in skipped[:10]:
            print(f"  {r}: {s}")

    if not args.confirm:
        print("\n[预演] 未删除任何文件。以下为前 15 条将被移入废纸篓的文件：")
        for e in todo[:15]:
            print(f"  {e['src']}")
        print("\n确认无误后加 --confirm 执行。")
        return

    print("\n" + "!" * 60)
    print("即将把上述源文件移入废纸篓（可在废纸篓还原）")
    print("!" * 60)
    ans = input("输入 yes 继续（其他任意键取消）: ").strip().lower()
    if ans != "yes":
        print("已取消。")
        return

    ok = fail = 0
    for e in todo:
        src = e["src"]
        try:
            base = os.path.basename(src)
            target = os.path.join(trash, base)
            i = 2
            while os.path.exists(target):
                stem, ext = os.path.splitext(base)
                target = os.path.join(trash, f"{stem} {i}{ext}")
                i += 1
            shutil.move(src, target)
            ok += 1
        except Exception as ex:
            fail += 1
            print(f"[失败] {src}: {ex}")

    print(f"\n已移入废纸篓 {ok} 个，失败 {fail} 个")

    # 清理变空的目录
    roots = {os.path.dirname(e["src"]) for e in todo}
    removed = 0
    for root in sorted(roots, key=len, reverse=True):
        d = root
        while d and d not in ("/", os.path.expanduser("~"), os.path.expanduser("~/Desktop")):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    removed += 1
                    d = os.path.dirname(d)
                else:
                    break
            except OSError:
                break
    if removed:
        print(f"清理空文件夹 {removed} 个")
    print(f"完成于 {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
