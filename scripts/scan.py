#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 1 — 递归扫描目录，产出文件台账 JSON。

用法:
    scan.py --root ~/Desktop --domain desktop --out scan_desktop.json
    scan.py --root ~/Desktop --domain desktop --append scan_all.json
    scan.py --root ~/Desktop --domain desktop --stats-only

设计要点:
    - 只读，绝不修改任何文件
    - 跳过隐藏文件、Office 锁文件、系统元数据
    - 不跟随符号链接（避免环）
"""
import argparse
import json
import os
import sys
from datetime import datetime

IGNORE_NAMES = {".DS_Store", ".localized", "desktop.ini", "Thumbs.db", "Icon\r"}


def should_skip(name: str) -> bool:
    if name in IGNORE_NAMES:
        return True
    # Office/WPS 锁文件、macOS 资源分叉、所有隐藏文件
    return name.startswith(".")


def human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return f"{nbytes:.1f}{unit}" if unit != "B" else f"{nbytes}B"
        nbytes /= 1024.0


def scan(root: str, domain: str) -> tuple:
    """返回 (items, errors)。"""
    root = os.path.abspath(os.path.expanduser(root))
    items, errors = [], []
    seen_links = set()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 剪枝：隐藏目录 + 符号链接目录
        pruned = []
        for d in dirnames:
            if d.startswith("."):
                continue
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                continue
            pruned.append(d)
        dirnames[:] = sorted(pruned)

        for fn in sorted(filenames):
            if should_skip(fn):
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.islink(fp):
                real = os.path.realpath(fp)
                if real in seen_links:
                    continue
                seen_links.add(real)
            try:
                st = os.stat(fp)
            except OSError as e:
                errors.append({"path": fp, "error": str(e)})
                continue

            stem, ext = os.path.splitext(fn)
            rel_dir = os.path.relpath(dirpath, root)
            origin_dir = "" if rel_dir == "." else rel_dir
            top_dir = origin_dir.split(os.sep)[0] if origin_dir else ""

            items.append({
                "path": fp,
                "name": fn,
                "stem": stem,
                "ext": ext.lstrip(".").lower(),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "source_domain": domain,
                "root": root,
                "origin_dir": origin_dir,
                "top_dir": top_dir,
            })
    return items, errors


def summarize(items) -> dict:
    total_size = sum(i["size"] for i in items)
    ext_count, top_count = {}, {}
    for i in items:
        ext_count[i["ext"] or "(无扩展名)"] = ext_count.get(i["ext"] or "(无扩展名)", 0) + 1
        key = i["top_dir"] or "(根目录散落文件)"
        top_count[key] = top_count.get(key, 0) + 1
    return {
        "file_count": len(items),
        "total_size": total_size,
        "total_size_human": human(total_size),
        "ext_distribution": dict(sorted(ext_count.items(), key=lambda x: -x[1])[:20]),
        "top_dir_distribution": dict(sorted(top_count.items(), key=lambda x: -x[1])),
    }


def main():
    ap = argparse.ArgumentParser(description="递归扫描目录生成文件台账")
    ap.add_argument("--root", required=True, help="扫描根目录")
    ap.add_argument("--domain", default="custom", help="来源域标记: desktop/downloads/wechat/custom")
    ap.add_argument("--out", help="输出 JSON 路径")
    ap.add_argument("--append", help="追加合并到已有 JSON")
    ap.add_argument("--stats-only", action="store_true", help="只输出统计，不写文件")
    args = ap.parse_args()

    root = os.path.expanduser(args.root)
    if not os.path.isdir(root):
        sys.exit(f"[错误] 目录不存在: {root}")

    items, errors = scan(root, args.domain)
    stats = summarize(items)

    print(f"扫描: {os.path.abspath(root)}  来源域: {args.domain}")
    print(f"文件数: {stats['file_count']}  总体积: {stats['total_size_human']}")
    print("扩展名 Top10:")
    for ext, cnt in list(stats["ext_distribution"].items())[:10]:
        print(f"  {ext:12s} {cnt}")
    if errors:
        print(f"[警告] {len(errors)} 个文件无法读取")
    if args.stats_only:
        return

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": os.path.abspath(root),
        "domain": args.domain,
        "stats": stats,
        "errors": errors,
        "items": items,
    }

    if args.append and os.path.exists(args.append):
        with open(args.append, "r", encoding="utf-8") as f:
            prev = json.load(f)
        prev_items = prev.get("items", [])
        known = {i["path"] for i in prev_items}
        merged = prev_items + [i for i in items if i["path"] not in known]
        prev["items"] = merged
        prev.setdefault("sources", []).append(
            {"root": payload["root"], "domain": args.domain, "stats": stats}
        )
        prev["stats"] = summarize(merged)
        prev["generated_at"] = datetime.now().isoformat(timespec="seconds")
        out = args.append
        payload = prev
    else:
        out = args.out or args.append

    if not out:
        sys.exit("[错误] 需要 --out、--append 或 --stats-only 之一")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"已写入: {out}  (台账总计 {len(payload['items'])} 条)")


if __name__ == "__main__":
    main()
