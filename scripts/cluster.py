#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 — 按文件名语义聚类，产出归档方案 plan.json（不移动任何文件）。

用法:
    cluster.py --scan scan_all.json --taxonomy taxonomy.json --out plan.json
    cluster.py --scan scan_all.json --taxonomy taxonomy.json --preview

打分规则（见 taxonomy.scoring）:
    strong 命中  +10/次，同类别封顶 20；关键词长度 >=4 额外 +2
    weak   命中  +3/次，封顶 9
    扩展名偏好   +2
    原目录名    精确等于分类名 +12 / 含 strong 词且为顶层目录 +8 / 深层 +5
    模板词命中  类别13 直接 +50（模板优先于内容）

冲突消解:
    1. 取最高分
    2. 同分取 priority 高者
    3. 全 0 分走 fallback 扩展名桶
    4. 系统垃圾文件单独归「系统杂项」，不参与主题聚类
    5. 文件数 < min_files_to_keep 的主题并入「其他主题」

置信度:
    high   — 至少命中一个领域专属词
    medium — 仅弱词命中，但分数 >= 5
    low    — 仅弱词命中且分数低，建议人工复核
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

FULLWIDTH = str.maketrans("（）［］｛｝：，、　", "()[]{}:,, ")

TEMPLATE_WORDS = ("模板", "template", "母版", "样例", "范例", "范式", "样板", "空表", "底稿")

HEX_NAME = re.compile(r"^[0-9a-f]{16,64}$", re.IGNORECASE)


def normalize(text: str, noise_tokens=None) -> str:
    """归一化：小写、全角转半角、去分隔符、去版本噪音。用于匹配，不改原文件名。"""
    t = text.lower().translate(FULLWIDTH)
    if noise_tokens:
        for pat in noise_tokens:
            try:
                t = re.sub(pat, " ", t, flags=re.IGNORECASE)
            except re.error:
                t = t.replace(pat, " ")
    t = re.sub(r"[\s_\-—–\.·]+", "", t)
    return t


class Clusterer:
    def __init__(self, taxonomy: dict):
        self.tax = taxonomy
        self.s = taxonomy.get("scoring", {})
        self.junk_cfg = taxonomy.get("junk", {})
        self.junk_patterns = []
        for pat in self.junk_cfg.get("name_patterns", []):
            try:
                self.junk_patterns.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                pass
        self.paper_prefix = None
        if self.junk_cfg.get("paper_prefix_pattern"):
            try:
                self.paper_prefix = re.compile(self.junk_cfg["paper_prefix_pattern"])
            except re.error:
                pass
        self.cats = taxonomy["categories"]
        self.noise = taxonomy.get("noise_tokens", [])
        self.compiled = []
        for c in self.cats:
            self.compiled.append({
                "cat": c,
                "name_n": normalize(c["name"]),
                "strong": [(normalize(k) if len(k) > 2 else k.lower(), k) for k in c.get("strong", [])],
                "weak": [(normalize(k) if len(k) > 2 else k.lower(), k) for k in c.get("weak", [])],
                "prefer": {e.lower() for e in c.get("prefer_ext", [])},
            })

    def is_junk(self, item: dict) -> str:
        """返回 junk 原因，空串表示不是垃圾。"""
        ext = item["ext"].lower()
        if ext in {e.lower() for e in self.junk_cfg.get("exts", [])}:
            return f"中间产物 .{ext}"
        name_l = item["name"].lower()
        for p in self.junk_cfg.get("name_prefixes", []):
            if name_l.startswith(p.lower()):
                return "临时文件"
        if self.junk_cfg.get("skip_zero_size") and item["size"] == 0:
            return "零字节文件"
        if self.junk_cfg.get("skip_hex_name") and item["ext"] == "" and HEX_NAME.match(item["stem"]):
            return "无意义哈希文件名"
        for pat in self.junk_patterns:
            if pat.search(item["name"]):
                return "导出残留文件"
        return ""

    def origin_score(self, origin_n: str, top_seg_n: str, entry: dict) -> tuple:
        """原目录名得分。

        顶层目录段代表用户的明确归类意图，权重高于文件名单个强词；
        深层子目录仅作辅助信号，权重较低。
        """
        if not origin_n:
            return 0.0, []
        if entry["name_n"] and entry["name_n"] in (origin_n, top_seg_n):
            return float(self.s.get("origin_equal_name", 12)), [f"目录名:{entry['cat']['name']}"]
        for kw_n, kw_raw in entry["strong"]:
            if kw_n and len(kw_n) >= 2 and kw_n in top_seg_n:
                return float(self.s.get("origin_contains_top", 14)), [f"顶层目录:{kw_raw}"]
        for kw_n, kw_raw in entry["strong"]:
            if kw_n and len(kw_n) >= 2 and kw_n in origin_n:
                return float(self.s.get("origin_contains_deep", 5)), [f"子目录:{kw_raw}"]
        return 0.0, []

    def score_file(self, item: dict):
        stem_n = normalize(item["stem"], self.noise)
        name_n = normalize(item["name"], self.noise)
        origin_dir = item.get("origin_dir", "")
        origin_n = normalize(origin_dir, self.noise)
        top_seg_n = normalize(origin_dir.split(os.sep)[0], self.noise) if origin_dir else ""
        ext = item["ext"].lower()
        is_template = any(w.lower() in name_n for w in TEMPLATE_WORDS)
        is_paper = bool(self.paper_prefix and self.paper_prefix.match(item["name"]))

        results = []
        for entry in self.compiled:
            cat = entry["cat"]
            strong_pts, hits, has_strong = 0.0, [], False

            for kw_n, kw_raw in entry["strong"]:
                if kw_n and kw_n in stem_n:
                    pts = self.s.get("strong_weight", 10)
                    if len(kw_raw) >= 4:
                        pts += 2
                    strong_pts = min(strong_pts + pts, self.s.get("strong_cap", 20))
                    hits.append(kw_raw)
                    has_strong = True

            weak_pts = 0.0
            for kw_n, kw_raw in entry["weak"]:
                if kw_n and kw_n in stem_n:
                    weak_pts = min(weak_pts + self.s.get("weak_weight", 3), self.s.get("weak_cap", 9))
                    hits.append(kw_raw)

            o_pts, o_hits = self.origin_score(origin_n, top_seg_n, entry)
            if o_hits:
                has_strong = True
                hits.extend(o_hits)

            score = strong_pts + weak_pts + o_pts
            # 扩展名加分只在有语义命中时生效，避免"光凭 .pdf 就归类"
            if score > 0 and ext and ext in entry["prefer"]:
                score += self.s.get("ext_bonus", 2)
            if is_template and cat["id"] == "13":
                score += 50
            if is_paper and cat["id"] == "16":
                score += 8

            # 领域词加成：领域专属词应压过「总结/报告/方案」这类通用文书类型词
            if score > 0:
                score *= cat.get("boost", 1.0)

            if score > 0:
                results.append({
                    "id": cat["id"], "name": cat["name"],
                    "priority": cat.get("priority", 0),
                    "score": round(score, 1), "hits": hits[:6],
                    "has_strong": has_strong,
                })

        results.sort(key=lambda r: (-r["score"], -r["priority"]))
        return results

    def fallback_bucket(self, ext: str) -> str:
        for bucket, exts in self.tax.get("fallback", {}).items():
            if ext in [e.lower() for e in exts]:
                return bucket
        return "待归类"


def confidence_of(top: dict) -> str:
    if top.get("has_strong"):
        return "high"
    return "medium" if top["score"] >= 5 else "low"


def main():
    ap = argparse.ArgumentParser(description="按文件名聚类生成归档方案")
    ap.add_argument("--scan", required=True)
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", default="plan.json")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    with open(args.scan, encoding="utf-8") as f:
        scan = json.load(f)
    with open(args.taxonomy, encoding="utf-8") as f:
        tax = json.load(f)

    items = scan["items"]
    cl = Clusterer(tax)
    min_keep = cl.s.get("min_files_to_keep", 5)
    delta = cl.s.get("ambiguous_delta", 2)

    assigned = defaultdict(lambda: {"files": [], "size": 0})
    ambiguous, junk, conf_count = [], [], defaultdict(int)

    for it in items:
        reason = cl.is_junk(it)
        if reason:
            assigned["99|系统杂项"]["files"].append({**it, "score": 0, "hits": [], "junk_reason": reason})
            assigned["99|系统杂项"]["size"] += it["size"]
            junk.append({"path": it["path"], "name": it["name"], "reason": reason})
            continue

        ranked = cl.score_file(it)
        if not ranked:
            bucket = cl.fallback_bucket(it["ext"])
            assigned[f"90|{bucket}"]["files"].append({**it, "score": 0, "hits": [], "confidence": "low"})
            assigned[f"90|{bucket}"]["size"] += it["size"]
            conf_count["low"] += 1
            continue

        top = ranked[0]
        conf = confidence_of(top)
        conf_count[conf] += 1
        assigned[f"{top['id']}|{top['name']}"]["files"].append(
            {**it, "score": top["score"], "hits": top["hits"], "confidence": conf})
        assigned[f"{top['id']}|{top['name']}"]["size"] += it["size"]

        if len(ranked) > 1 and (top["score"] - ranked[1]["score"]) <= delta and ranked[1]["score"] > 0:
            ambiguous.append({
                "path": it["path"], "name": it["name"],
                "chosen": top["name"], "chosen_score": top["score"],
                "runner_up": ranked[1]["name"], "runner_score": ranked[1]["score"],
            })

    other_files, other_size = [], 0
    merged_names = []

    # 先把结果拆成「主主题」与「兜底桶」
    raw = []
    for key, data in sorted(assigned.items()):
        cid, cname = key.split("|", 1)
        is_misc = cid.startswith(("90", "99"))
        folder = f"_杂项/{cname}" if is_misc else f"{cid}_{cname}"
        high = sum(1 for f in data["files"] if f.get("confidence") == "high")
        raw.append({"id": cid, "name": cname, "folder": folder,
                    "files": data["files"], "size": data["size"], "high": high})

    main_cats = [c for c in raw if not c["id"].startswith(("90", "95", "99"))]
    misc_cats = [c for c in raw if c["id"].startswith(("90", "99"))]

    # 兜底桶里过小的（<3）并入「其他素材」
    for c in list(misc_cats):
        if c["id"] == "90" and len(c["files"]) < 3:
            other_files.extend(c["files"])
            other_size += c["size"]
            merged_names.append(f"{c['name']}({len(c['files'])})")
            misc_cats.remove(c)

    # 主题总数超限时，按体积从小到大合并「无高置信文件」的弱势主题
    max_cats = cl.s.get("max_categories", 18)
    if len(main_cats) + len(misc_cats) > max_cats:
        candidates = sorted([c for c in main_cats if c["high"] == 0], key=lambda c: len(c["files"]))
        for c in candidates:
            if len(main_cats) + len(misc_cats) <= max_cats:
                break
            main_cats.remove(c)
            other_files.extend(c["files"])
            other_size += c["size"]
            merged_names.append(f"{c['name']}({len(c['files'])})")

    categories = []
    for c in main_cats + misc_cats:
        categories.append({
            "id": c["id"], "name": c["name"], "folder": c["folder"],
            "count": len(c["files"]), "size": c["size"],
            "files": sorted(c["files"], key=lambda x: x["name"]),
        })

    if other_files:
        categories.append({
            "id": "95", "name": "其他素材", "folder": "_杂项/其他素材",
            "count": len(other_files), "size": other_size,
            "files": sorted(other_files, key=lambda x: x["name"]),
        })

    categories.sort(key=lambda c: c["folder"])

    plan = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_scan": args.scan,
        "taxonomy": tax.get("name", ""),
        "total_files": len(items),
        "total_size": sum(i["size"] for i in items),
        "category_count": len(categories),
        "confidence": dict(conf_count),
        "merged_low_freq": merged_names,
        "ambiguous_count": len(ambiguous),
        "ambiguous": sorted(ambiguous, key=lambda x: (x["chosen_score"] - x["runner_score"]))[:80],
        "junk_count": len(junk),
        "junk": junk[:200],
        "categories": categories,
    }

    print(f"总文件 {plan['total_files']}  主题 {plan['category_count']}  "
          f"杂项 {len(junk)}  存疑 {len(ambiguous)}")
    print(f"置信度: high {conf_count.get('high',0)}  medium {conf_count.get('medium',0)}  low {conf_count.get('low',0)}")
    print("-" * 74)
    print(f"{'文件夹':34s} {'文件数':>7s} {'体积':>10s} {'高置信':>7s}")
    print("-" * 74)
    for c in categories:
        hi = sum(1 for f in c["files"] if f.get("confidence") == "high")
        mb = c["size"] / 1024 / 1024
        print(f"{c['folder'][:34]:34s} {c['count']:>7d} {mb:>9.1f}M {hi:>7d}")
    if merged_names:
        print(f"\n低频合并: {', '.join(merged_names)}")

    if not args.preview:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
        print(f"\n方案已写入: {args.out}")


if __name__ == "__main__":
    main()
