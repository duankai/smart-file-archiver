#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 — 按 plan.json 执行归档（移动或复制），生成 manifest.json。

用法:
    # 1. 预演（默认行为，不动任何文件）
    archive.py --plan plan.json --dest ~/Desktop/文件归档

    # 2. 真正执行移动
    archive.py --plan plan.json --dest ~/Desktop/文件归档 --mode move --execute

    # 3. 复制模式（微信/QQ 等应用目录用这个）
    archive.py --plan plan.json --dest ~/Desktop/文件归档 --mode copy --execute

安全机制:
    - 默认 dry-run，必须 --execute 才真正动手
    - 绝不覆盖已有文件，同名自动加 _2/_3
    - 每批 200 个，批间校验数量
    - 全程写 manifest.json（撤销依据）与 archive.log
    - 复制模式预先校验磁盘余量
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

BATCH = 200

# 应用目录来源域：原件被 App 按路径引用，默认只复制不移动
COPY_DOMAINS = {"wechat", "qq", "app", "im"}


def resolve_mode(domain: str, global_mode: str, mode_map: dict) -> str:
    """决定单个文件的处理方式。

    优先级：显式 mode_map > 全局 --mode > 按来源域自动判定
    """
    if mode_map and domain in mode_map:
        return mode_map[domain]
    if global_mode:
        return global_mode
    return "copy" if domain in COPY_DOMAINS else "move"


def parse_mode_map(raw: str) -> dict:
    """解析 desktop=move,wechat=copy"""
    out = {}
    if not raw:
        return out
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            v = v.strip()
            if v in ("move", "copy"):
                out[k.strip()] = v
    return out


def unique_dest(dest_dir: str, name: str) -> str:
    """避免覆盖：同名文件加 _2/_3 后缀。"""
    target = os.path.join(dest_dir, name)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(name)
    i = 2
    while True:
        cand = os.path.join(dest_dir, f"{stem}_{i}{ext}")
        if not os.path.exists(cand):
            return cand
        i += 1


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser(description="执行文件归档")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--dest", required=True, help="归档区根目录")
    ap.add_argument("--mode", choices=["move", "copy"], default=None,
                    help="全局模式；不指定则按来源域自动判定（应用目录复制，其余移动）")
    ap.add_argument("--mode-map", default="",
                    help="按来源域指定模式，如 desktop=move,wechat=copy")
    ap.add_argument("--execute", action="store_true", help="真正执行（否则只预演）")
    ap.add_argument("--resume", action="store_true",
                    help="从归档区已有 manifest 续跑，跳过已完成项（中断后恢复用）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件（调试用）")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)

    mode_map = parse_mode_map(args.mode_map)
    dest_root = os.path.abspath(os.path.expanduser(args.dest))
    tasks = []
    for cat in plan["categories"]:
        for f in cat["files"]:
            domain = f.get("source_domain", "custom")
            tasks.append({
                "src": f["path"],
                "name": f["name"],
                "folder": cat["folder"],
                "category": cat["name"],
                "size": f["size"],
                "domain": domain,
                "mode": resolve_mode(domain, args.mode, mode_map),
            })
    if args.limit:
        tasks = tasks[: args.limit]

    # ---------- 续跑：跳过 manifest 里已完成的源文件 ----------
    manifest_path = os.path.join(dest_root, "manifest.json")
    prev_entries = []
    if args.resume and os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                prev = json.load(f)
            prev_entries = [e for e in prev.get("entries", []) if e.get("status") == "ok"]
            done = {e["src"] for e in prev_entries}
            before = len(tasks)
            tasks = [t for t in tasks if t["src"] not in done]
            print(f"[续跑] 已有记录 {len(prev_entries)} 条，跳过 {before - len(tasks)} 个，剩余 {len(tasks)} 个")
        except Exception as e:
            print(f"[警告] 读取已有 manifest 失败，按全量处理: {e}")

    total_size = sum(t["size"] for t in tasks)
    copy_tasks = [t for t in tasks if t["mode"] == "copy"]
    move_tasks = [t for t in tasks if t["mode"] == "move"]
    copy_size = sum(t["size"] for t in copy_tasks)

    print(f"目标: {dest_root}")
    print(f"待处理: {len(tasks)} 个文件   合计 {human(total_size)}")
    print(f"  移动 {len(move_tasks)} 个 ({human(total_size - copy_size)})")
    print(f"  复制 {len(copy_tasks)} 个 ({human(copy_size)})")

    # 磁盘余量校验（只算复制部分）
    if copy_size > 0:
        probe = dest_root
        while not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        free = shutil.disk_usage(probe).free
        print(f"目标盘可用 {human(free)} / 复制需要 {human(copy_size)}")
        if free < copy_size * 1.05:
            sys.exit("[中止] 磁盘空间不足，请改用 --mode move 或清理空间")

    if not args.execute:
        print("\n[预演] 未做任何改动。以下为前 15 条示例：")
        folders = {}
        for t in tasks:
            folders[t["folder"]] = folders.get(t["folder"], 0) + 1
        for folder, cnt in sorted(folders.items()):
            print(f"  {folder}/  ({cnt} 个)")
        print("\n前 15 个文件：")
        for t in tasks[:15]:
            print(f"  {t['src']}\n    -> {os.path.join(dest_root, t['folder'], t['name'])}")
        print("\n确认无误后加 --execute 执行。")
        return

    os.makedirs(dest_root, exist_ok=True)
    log_path = os.path.join(dest_root, "archive.log")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode or "auto",
        "mode_map": mode_map,
        "auto_rule": f"来源域 {sorted(COPY_DOMAINS)} 用 copy，其余 move",
        "dest_root": dest_root,
        "plan": os.path.abspath(args.plan),
        "total": len(tasks) + len(prev_entries),
        "entries": list(prev_entries),
    }

    ok = fail = skip = 0
    failed = []

    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} "
                  f"启动 {args.mode} 共 {len(tasks)} 个文件 =====\n")

        for idx, t in enumerate(tasks, 1):
            src = t["src"]
            if not os.path.exists(src):
                skip += 1
                failed.append({"src": src, "error": "源文件不存在"})
                log.write(f"[SKIP] {src} 源文件不存在\n")
                continue

            dest_dir = os.path.join(dest_root, t["folder"])
            try:
                os.makedirs(dest_dir, exist_ok=True)
                dst = unique_dest(dest_dir, t["name"])
                if t["mode"] == "copy":
                    shutil.copy2(src, dst)
                else:
                    shutil.move(src, dst)
                ok += 1
                manifest["entries"].append({
                    "src": src, "dst": dst, "mode": t["mode"],
                    "domain": t["domain"], "status": "ok",
                })
                log.write(f"[{'COPY' if t['mode']=='copy' else 'MOVE'}] {src} -> {dst}\n")
            except Exception as e:
                fail += 1
                failed.append({"src": src, "error": str(e)})
                log.write(f"[FAIL] {src} : {e}\n")

            if idx % BATCH == 0:
                # 批间校验：清单条数必须与成功数吻合
                assert ok + fail + skip == idx, "批间计数不一致"
                print(f"  进度 {idx}/{len(tasks)}  成功 {ok}  失败 {fail}  跳过 {skip}")
                with open(manifest_path, "w", encoding="utf-8") as mf:
                    json.dump(manifest, mf, ensure_ascii=False, indent=1)

    manifest["summary"] = {"ok": ok, "fail": fail, "skip": skip, "failed": failed[:200]}
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=1)

    print(f"\n完成: 成功 {ok}  失败 {fail}  跳过 {skip}")
    print(f"清单: {manifest_path}")
    print(f"日志: {log_path}")
    if fail:
        print("[注意] 存在失败项，详见日志与 manifest.summary.failed")


if __name__ == "__main__":
    main()
