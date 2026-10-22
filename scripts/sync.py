#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 7 — 增量同步（Incremental Sync）。

只处理「上次同步时间之后」的新文件，自动归入细分类、复制进归档区并追加索引。
水位线（watermark）存在 <归档区>/sync_state.json，按来源域分别记录。

用法:
    # 预演：看看微信自上次以来新增了什么（不复制任何文件）
    sync.py --archive ~/Desktop/文件归档 --source wechat

    # 真正执行
    sync.py --archive ~/Desktop/文件归档 --source wechat --execute

    # 首次接入时忽略水位线，把历史文件全部纳入
    sync.py --archive ~/Desktop/文件归档 --source wechat --all --execute

    # 指定起点（ISO 时间）
    sync.py --archive ~/Desktop/文件归档 --source wechat --since 2026-09-01T00:00:00

产出:
    <归档区>/_增量/<YYYY-MM>/<细分类>/<文件>
    <归档区>/index.json     追加新条目（含细分类标签）
    <归档区>/sync_state.json 更新水位线
"""
import argparse
import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from refine import Refiner  # 复用细分类打分逻辑
except ImportError:
    Refiner = None

WECHAT_DEFAULT = ("~/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
                  "xwechat_files")
SKIP_DIRS = {"_tool", "_增量", "node_modules", ".git"}
TS_FMT = "%Y-%m-%dT%H:%M:%S"


def find_wechat_root():
    base = os.path.expanduser(WECHAT_DEFAULT)
    if not os.path.isdir(base):
        return ""
    for name in sorted(os.listdir(base)):
        cand = os.path.join(base, name, "msg", "file")
        if os.path.isdir(cand):
            return cand
    return ""


def human(n):
    if n < 1024:
        return f"{n}B"
    for u in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f}{u}"
    return f"{n:.1f}PB"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def unique_dest(folder, name):
    dst = os.path.join(folder, name)
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(name)
    i = 2
    while True:
        cand = os.path.join(folder, f"{stem}_{i}{ext}")
        if not os.path.exists(cand):
            return cand
        i += 1


def iter_files(root, since_ts, archive_root):
    """递归列出 root 下 mtime > since_ts 的文件。"""
    root_real = os.path.realpath(root)
    archive_real = os.path.realpath(archive_root)
    for dirpath, dirnames, filenames in os.walk(root_real):
        # 跳过归档区自身与工具目录
        if os.path.realpath(dirpath).startswith(archive_real):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or fn.endswith((".tmp", ".part", ".download")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if since_ts and st.st_mtime <= since_ts:
                continue
            if st.st_size == 0:
                continue
            yield p, st


def main():
    ap = argparse.ArgumentParser(description="增量同步新文件进归档区")
    ap.add_argument("--archive", required=True, help="归档区根目录")
    ap.add_argument("--source", required=True,
                    help="来源域：wechat / desktop / 自定义名称")
    ap.add_argument("--root", help="来源目录（默认按 source 推断，wechat 自动探测）")
    ap.add_argument("--taxonomy", default=os.path.join(
        os.path.dirname(HERE), "examples", "taxonomy_v3.json"))
    ap.add_argument("--since", help="起始时间 ISO，默认读水位线")
    ap.add_argument("--all", action="store_true", help="忽略水位线，全量纳入")
    ap.add_argument("--execute", action="store_true", help="真正复制并写索引")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个（调试）")
    args = ap.parse_args()

    archive = os.path.abspath(os.path.expanduser(args.archive))
    index_path = os.path.join(archive, "index.json")
    state_path = os.path.join(archive, "sync_state.json")
    if not os.path.exists(index_path):
        sys.exit(f"[错误] 找不到索引: {index_path}")

    # 进程锁：避免定时任务与手动执行并发写索引（会导致重复复制）
    lock_path = os.path.join(archive, ".sync.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("[跳过] 已有同步任务在运行（.sync.lock 被占用），本次退出。")

    # ---- 来源目录 ----
    if args.root:
        root = os.path.abspath(os.path.expanduser(args.root))
    elif args.source == "wechat":
        root = find_wechat_root()
        if not root:
            sys.exit("[错误] 未找到微信接收文件目录，请用 --root 指定")
    elif args.source == "desktop":
        root = os.path.expanduser("~/Desktop")
    else:
        sys.exit("[错误] 未知来源，请用 --root 指定目录")
    if not os.path.isdir(root):
        sys.exit(f"[错误] 来源目录不存在: {root}")

    # ---- 水位线 ----
    state = load_json(state_path, {"sources": {}})
    src_state = state.setdefault("sources", {}).setdefault(
        args.source, {"watermark": "", "runs": 0, "synced": 0})
    watermark = "" if args.all else (args.since or src_state.get("watermark", ""))
    since_ts = 0.0
    if watermark:
        try:
            since_ts = time.mktime(time.strptime(watermark[:19], TS_FMT))
        except ValueError:
            since_ts = 0.0

    index = load_json(index_path, {"items": []})
    known_paths = {it["path"] for it in index["items"]}
    known_sig = {(it["name"], it.get("size", 0), it.get("mtime", ""))
                 for it in index["items"]}

    # ---- 细分类引擎 ----
    refiner = None
    if Refiner:
        tax = load_json(os.path.expanduser(args.taxonomy), None)
        if tax:
            refiner = Refiner(tax)

    # ---- 收集新增 ----
    new_files = []
    for p, st in iter_files(root, since_ts, archive):
        mtime = time.strftime(TS_FMT, time.localtime(st.st_mtime))
        name = os.path.basename(p)
        if p in known_paths or (name, st.st_size, mtime) in known_sig:
            continue
        new_files.append({"src": p, "name": name, "size": st.st_size,
                          "mtime": mtime, "ext": os.path.splitext(name)[1].lstrip(".").lower()})
        if args.limit and len(new_files) >= args.limit:
            break

    print("=" * 60)
    print(f"来源: {args.source}  ({root})")
    print(f"水位线: {watermark or '未设置（全量）'}")
    print(f"归档区: {archive}")
    print(f"发现新增/变更文件: {len(new_files)} 个，合计 {human(sum(f['size'] for f in new_files))}")
    print("=" * 60)
    if not new_files:
        print("没有需要同步的新文件。")
        return

    # ---- 分配细分类 ----
    buckets = {}
    for f in new_files:
        if refiner:
            c, conf, pts, _, _, _ = refiner.assign({"name": f["name"], "ext": f["ext"],
                                                    "origin": args.source})
            f.update(fine_id=c["id"], fine_name=c["name"], fine_group=c.get("group", ""),
                     fine_conf=conf, fine_score=round(pts, 1))
        else:
            f.update(fine_id="Z0", fine_name="未分类", fine_group="H 材料与其他",
                     fine_conf="low", fine_score=0)
        buckets.setdefault(f["fine_name"], []).append(f)

    print("\n按细分类分布：")
    for name, lst in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print(f"  {name:12s} {len(lst):4d} 个  {human(sum(f['size'] for f in lst))}")

    if not args.execute:
        print("\n[预演] 未做任何改动。加 --execute 才会复制并写索引。")
        for f in new_files[:10]:
            print(f"  {f['name'][:48]:50s} → {f['fine_name']}")
        if len(new_files) > 10:
            print(f"  … 其余 {len(new_files) - 10} 个")
        return

    # ---- 执行：复制 + 追加索引 ----
    month = datetime.now().strftime("%Y-%m")
    copied, failed = 0, []
    max_mtime = watermark
    for f in new_files:
        folder = os.path.join(archive, "_增量", month,
                              f"{f['fine_id']}_{f['fine_name']}")
        os.makedirs(folder, exist_ok=True)
        dst = unique_dest(folder, f["name"])
        try:
            shutil.copy2(f["src"], dst)
        except Exception as e:
            failed.append({"src": f["src"], "error": str(e)})
            continue
        copied += 1
        if f["mtime"] > max_mtime:
            max_mtime = f["mtime"]
        index["items"].append({
            "name": f["name"], "path": dst, "ext": f["ext"], "size": f["size"],
            "mtime": f["mtime"], "category": "增量同步",
            "folder": f"_增量/{month}/{f['fine_id']}_{f['fine_name']}",
            "origin": f"增量:{args.source}", "source_domain": args.source,
            "confidence": f["fine_conf"], "score": f["fine_score"], "hits": [],
            "exists": True,
            "fine_id": f["fine_id"], "fine_name": f["fine_name"],
            "fine_group": f["fine_group"], "fine_conf": f["fine_conf"],
            "fine_score": f["fine_score"],
        })

    index["count"] = len(index["items"])
    index["incremental_at"] = datetime.now().isoformat(timespec="seconds")
    # 刷新细分类计数
    counts = {}
    for it in index["items"]:
        n = it.get("fine_name")
        if n:
            counts[n] = counts.get(n, 0) + 1
    if index.get("fine_categories"):
        for c in index["fine_categories"]:
            c["count"] = counts.get(c["name"], c["count"] if c["name"] not in counts else c["count"])

    write_json(index_path, index)
    src_state.update({
        "watermark": max_mtime or time.strftime(TS_FMT),
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "runs": src_state.get("runs", 0) + 1,
        "synced": src_state.get("synced", 0) + copied,
        "root": root,
    })
    write_json(state_path, state)

    print(f"\n完成：复制 {copied} 个，失败 {len(failed)} 个")
    print(f"索引总计 {index['count']} 条，水位线更新为 {src_state['watermark']}")
    if failed:
        print("失败明细：")
        for x in failed[:5]:
            print(f"  {x['src']}: {x['error']}")


if __name__ == "__main__":
    main()
