#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 — 生成索引：INDEX.md（人读）、index.csv（WPS/Excel）、index.json（Web 工具）。

用法:
    index_gen.py --plan plan.json --dest ~/Desktop/文件归档
    index_gen.py --plan plan.json --dest ~/Desktop/文件归档 --manifest ~/Desktop/文件归档/manifest.json

说明:
    优先使用 manifest.json 中的实际落地路径（含同名冲突改名），
    这样索引里的 path 一定是真实可打开的路径。
"""
import argparse
import csv
import json
import os
from datetime import datetime

ARCHIVE_NOTE = "本索引由 smart-file-archiver 生成。搜索建议使用随附的「启动文件搜索.command」。"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024.0


def load_manifest_path(mf_path: str) -> dict:
    if not mf_path or not os.path.exists(mf_path):
        return {}
    with open(mf_path, encoding="utf-8") as f:
        mf = json.load(f)
    mapping = {}
    for e in mf.get("entries", []):
        if e.get("status") == "ok":
            mapping[e["src"]] = e["dst"]
    return mapping


def main():
    ap = argparse.ArgumentParser(description="生成归档索引")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--manifest", default="")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    dest_root = os.path.abspath(os.path.expanduser(args.dest))
    mf_map = load_manifest_path(args.manifest) if args.manifest else {}

    records = []
    for cat in plan["categories"]:
        for f in cat["files"]:
            src = f["path"]
            if src in mf_map:
                final_path = mf_map[src]
            else:
                final_path = os.path.join(dest_root, cat["folder"], f["name"])
            records.append({
                "name": f["name"],
                "path": final_path,
                "ext": f.get("ext", ""),
                "size": f.get("size", 0),
                "mtime": f.get("mtime", ""),
                "category": cat["name"],
                "folder": cat["folder"],
                "origin": f.get("origin_dir", ""),
                "source_domain": f.get("source_domain", ""),
                "confidence": f.get("confidence", ""),
                "score": f.get("score", 0),
                "hits": f.get("hits", []),
                "exists": os.path.exists(final_path),
            })

    records.sort(key=lambda r: (r["category"], r["name"].lower()))

    # ---------- index.json ----------
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "archive_root": dest_root,
        "count": len(records),
        "categories": [
            {"name": c["name"], "folder": c["folder"], "count": c["count"]}
            for c in plan["categories"]
        ],
        "items": records,
    }
    json_path = os.path.join(dest_root, "index.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    # ---------- index.csv（utf-8-sig 便于 WPS/Excel 直接打开）----------
    csv_path = os.path.join(dest_root, "index.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "文件名", "分类", "扩展名", "大小", "修改时间", "原目录", "完整路径", "置信度"])
        for i, r in enumerate(records, 1):
            w.writerow([i, r["name"], r["category"], r["ext"], human(r["size"]),
                        r["mtime"], r["origin"], r["path"], r["confidence"]])

    # ---------- INDEX.md ----------
    md_path = os.path.join(dest_root, "INDEX.md")
    by_cat = {}
    for r in records:
        by_cat.setdefault(r["category"], []).append(r)

    lines = [
        "# 文件归档总索引",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 归档位置：`{dest_root}`",
        f"- 文件总数：**{len(records)}**",
        f"- 分类数：**{len(plan['categories'])}**",
        "",
        "## 分类目录",
        "",
        "| 文件夹 | 文件数 | 体积 | 说明 |",
        "|---|---:|---:|---|",
    ]
    for c in plan["categories"]:
        lines.append(f"| `{c['folder']}` | {c['count']} | {human(c['size'])} | {c['name']} |")

    lines += ["", "---", ""]
    for c in plan["categories"]:
        items = by_cat.get(c["name"], [])
        if not items:
            continue
        lines.append(f"## {c['folder']}（{len(items)} 个）")
        lines.append("")
        lines.append("| 文件名 | 大小 | 修改时间 | 原目录 |")
        lines.append("|---|---:|---|---|")
        for r in items:
            name = r["name"].replace("|", "\\|")
            lines.append(f"| {name} | {human(r['size'])} | {r['mtime'][:10]} | {r['origin'] or '—'} |")
        lines.append("")

    lines += ["---", "", f"> {ARCHIVE_NOTE}", ""]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    missing = sum(1 for r in records if not r["exists"])
    print(f"索引已生成：")
    print(f"  {md_path}")
    print(f"  {csv_path}")
    print(f"  {json_path}")
    print(f"记录 {len(records)} 条，分类 {len(plan['categories'])} 个")
    if missing:
        print(f"[提示] {missing} 条记录的路径当前不存在（预演阶段正常，归档后重新生成即可）")


if __name__ == "__main__":
    main()
