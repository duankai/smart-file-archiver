#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rebuild_manifest.py — 从 archive.log 反向重建 manifest.json

manifest.json 是撤销（rollback.py）的唯一依据。它可能因为中断、进程被杀、
误删而丢失或残缺，但 archive.log 是逐条追加写的，通常更完整——本脚本就用日志
把清单重建出来。

做法：
  * 逐行解析 `[COPY] src -> dst` / `[MOVE] src -> dst` / `[FAIL] src : err` / `[SKIP] src ...`
  * 按 (src, dst) 去重，同一个文件多次记录时保留最后一次（续跑会重复写日志）
  * 来源域按源路径推断（微信 / QQ / 应用容器 / 下载 / 桌面 / 其他）
  * 可选 --verify 逐条确认 dst 真实存在，把缺失项剔出清单并单独列出
  * 覆盖前自动备份旧 manifest

用法：
    python3 scripts/rebuild_manifest.py                          # 默认 ~/Desktop/文件归档
    python3 scripts/rebuild_manifest.py --dest ~/Desktop/文件归档 --dry-run
    python3 scripts/rebuild_manifest.py --dest ~/Desktop/文件归档 --verify
    python3 scripts/rebuild_manifest.py --log a.log --out m.json --plan plan_all.json

依赖：仅标准库
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

RE_OK = re.compile(r"^\[(MOVE|COPY)\]\s+(.+?)\s+->\s+(.+)$")
RE_FAIL = re.compile(r"^\[FAIL\]\s+(.+?)\s+:\s+(.+)$")
RE_SKIP = re.compile(r"^\[SKIP\]\s+(.+?)(?:\s{2,}|\s+源文件不存在)?$")
RE_HEAD = re.compile(r"^=====\s+(\S+)\s+启动\s+(\S+)\s+共\s+(\d+)\s+个文件")

# 来源域推断：命中即归类，顺序从具体到宽泛
DOMAIN_RULES = [
    ("wechat", ("xwechat_files", "com.tencent.xinwechat")),
    ("qq", ("com.tencent.qq", "qq_files")),
    ("app", ("/library/containers/",)),
    ("downloads", ("/downloads/",)),
    ("desktop", ("/desktop/",)),
]


def infer_domain(path: str) -> str:
    low = path.lower()
    for domain, needles in DOMAIN_RULES:
        if any(n in low for n in needles):
            return domain
    return "custom"


def parse_log(log_path: str):
    """返回 (entries, failed, skipped, sessions)"""
    entries: dict[tuple[str, str], dict] = {}
    failed: dict[str, str] = {}
    skipped: list[str] = []
    sessions: list[dict] = []
    cur_ts = ""

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = RE_HEAD.match(line)
            if m:
                cur_ts = m.group(1)
                sessions.append({"ts": cur_ts, "mode": m.group(2), "total": int(m.group(3))})
                continue
            m = RE_OK.match(line)
            if m:
                mode = "move" if m.group(1) == "MOVE" else "copy"
                src, dst = m.group(2), m.group(3)
                entries[(src, dst)] = {
                    "src": src,
                    "dst": dst,
                    "mode": mode,
                    "domain": infer_domain(src),
                    "status": "ok",
                    "note": "rebuilt from archive.log",
                }
                if cur_ts and "ts" not in entries[(src, dst)]:
                    entries[(src, dst)]["ts"] = cur_ts
                continue
            m = RE_FAIL.match(line)
            if m:
                failed[m.group(1)] = m.group(2).strip()
                continue
            m = RE_SKIP.match(line)
            if m:
                skipped.append(m.group(1).strip())

    return list(entries.values()), failed, skipped, sessions


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def main() -> None:
    ap = argparse.ArgumentParser(description="从 archive.log 重建 manifest.json")
    ap.add_argument("--dest", default=os.path.expanduser("~/Desktop/文件归档"),
                    help="归档区根目录（日志与清单的默认位置）")
    ap.add_argument("--log", default="", help="日志路径，默认 <dest>/archive.log")
    ap.add_argument("--out", default="", help="输出清单，默认 <dest>/manifest.json")
    ap.add_argument("--plan", default="", help="可选：plan.json，用于回填计划总数")
    ap.add_argument("--verify", action="store_true", help="逐条确认 dst 仍存在")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--no-backup", action="store_true", help="覆盖时不备份旧清单")
    args = ap.parse_args()

    dest = os.path.abspath(os.path.expanduser(args.dest))
    log_path = os.path.abspath(os.path.expanduser(args.log)) if args.log else os.path.join(dest, "archive.log")
    out_path = os.path.abspath(os.path.expanduser(args.out)) if args.out else os.path.join(dest, "manifest.json")

    if not os.path.exists(log_path):
        sys.exit(f"[错误] 找不到日志: {log_path}")

    entries, failed, skipped, sessions = parse_log(log_path)
    moves = sum(1 for e in entries if e["mode"] == "move")
    copies = len(entries) - moves

    print(f"日志: {log_path}")
    print(f"  段落(运行批次) {len(sessions)}   成功记录 {len(entries)} 条（移动 {moves} / 复制 {copies}）")
    print(f"  失败 {len(failed)} 条   跳过 {len(skipped)} 条")

    missing: list[str] = []
    if args.verify:
        for e in entries:
            if not os.path.exists(e["dst"]):
                missing.append(e["dst"])
        if missing:
            keep = [e for e in entries if os.path.exists(e["dst"])]
            print(f"  [校验] {len(missing)} 条副本已不存在，已从清单剔除")
            entries = keep
        else:
            print("  [校验] 所有副本均存在")

    by_domain: dict[str, int] = {}
    total_size = 0.0
    for e in entries:
        by_domain[e["domain"]] = by_domain.get(e["domain"], 0) + 1
        try:
            total_size += os.path.getsize(e["dst"])
        except OSError:
            pass
    print("  来源域分布: " + "  ".join(f"{k}={v}" for k, v in sorted(by_domain.items())))
    if total_size:
        print(f"  副本合计约 {human(total_size)}")

    total = None
    if args.plan:
        try:
            with open(args.plan, encoding="utf-8") as f:
                plan = json.load(f)
            total = sum(len(c["files"]) for c in plan["categories"])
            print(f"  计划总数 {total}，未完成 {max(0, total - len(entries))}")
        except Exception as e:
            print(f"  [提示] 读取 plan 失败，跳过总数回填: {e}")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "auto",
        "mode_map": {},
        "auto_rule": "来源域 wechat/qq/app/im 用 copy，其余 move",
        "dest_root": dest,
        "plan": args.plan or None,
        "total": total if total is not None else len(entries),
        "entries": entries,
        "summary": {
            "ok": len(entries),
            "fail": len(failed),
            "skip": len(skipped),
            "failed": [{"src": s, "error": err} for s, err in list(failed.items())[:200]],
            "skipped": skipped[:200],
            "missing": missing[:200],
            "note": "本清单由 archive.log 重建"
                    + ("，已剔除副本缺失的记录" if missing else ""),
        },
    }

    if args.dry_run:
        print(f"\n[预演] 未写入。将写入 {out_path}，条目 {len(entries)} 条")
        return

    if os.path.exists(out_path) and not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{out_path}.bak-{stamp}"
        os.replace(out_path, bak)
        print(f"\n已备份旧清单 -> {bak}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"已写入 {out_path}")
    print(f"提示：清单只记录移动/复制动作，重跑一次 archive.py --resume 会自动改用它跳过已完成项。")


if __name__ == "__main__":
    main()
