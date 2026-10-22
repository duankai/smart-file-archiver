#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 3.5 — 细分类打标（Refine）。

在已有归档结果之上，给每个文件追加细分类标签（不移动任何文件）：
    index.json 的每个 item 增加 fine_id / fine_name / fine_group / fine_conf

用法:
    refine.py --index ~/Desktop/文件归档/index.json \
              --taxonomy examples/taxonomy_v3.json --report
    refine.py --index index.json --taxonomy taxonomy_v3.json --write
"""
import argparse
import collections
import json
import os
import re
import sys
import tempfile
from datetime import datetime

SEP = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
LATIN = re.compile(r"[A-Za-z]+")

# 文件名里对语义判断无贡献的噪声词
NOISE = {
    "副本", "复制", "最新", "最终", "修改", "初稿", "终稿", "定稿", "草稿",
    "版本", "附件", "文件", "文档", "材料", "关于", "以及", "进行", "一个",
    "final", "draft", "copy", "new", "old", "doc", "document", "temp",
}

# 零命中文件的归属，避免按优先级被硬塞进靠前的分类
UNCLASSIFIED = {"id": "Z0", "name": "未分类", "group": "H 材料与其他", "priority": 0}


def normalize(s: str) -> str:
    s = (s or "").lower()
    s = SEP.sub("", s)
    return s


class Refiner:
    def __init__(self, taxonomy):
        self.s = taxonomy.get("scoring", {})
        self.cats = []
        for c in taxonomy["categories"]:
            strong = [w for w in c.get("strong", []) if w]
            self.cats.append({
                "id": c["id"], "name": c["name"], "group": c.get("group", ""),
                "boost": float(c.get("boost", 1.0)),
                "priority": int(c.get("priority", 0)),
                "strong": [(normalize(w), w) for w in strong],
                "weak": [(normalize(w), w) for w in c.get("weak", [])],
                "exts": {e.lower() for e in c.get("prefer_ext", [])},
            })
        # 关键词区分度：出现在越多类别里的词，判别力越弱，权重按此衰减
        self.spec = {}
        for attr in ("strong", "weak"):
            cnt = collections.Counter()
            for c in self.cats:
                seen = {kw_n for kw_n, _ in c[attr] if kw_n}
                for kw_n in seen:
                    cnt[kw_n] += 1
            for kw_n, n in cnt.items():
                self.spec[(attr, kw_n)] = 1.0 if n <= 1 else (0.6 if n <= 3 else 0.25)

    def _w(self, attr, kw_n):
        return self.spec.get((attr, kw_n), 1.0)

    @staticmethod
    def _hit(kw_n, kw, name_n, name_raw):
        """短英文词（如 cv、ai）按词边界匹配，避免 asyncvideo 命中 cv。"""
        if kw_n.isascii() and len(kw_n) <= 3:
            return re.search(r"(?<![a-z0-9])" + re.escape(kw_n) + r"(?![a-z0-9])",
                             name_raw) is not None
        return kw_n in name_n

    def score(self, item):
        name_n = normalize(item["name"])
        name_raw = (item["name"] or "").lower()
        origin_n = normalize(item.get("origin", ""))
        ext = (item.get("ext") or "").lower()
        results = []

        for c in self.cats:
            strong_pts, hits = 0.0, []
            for kw_n, kw in c["strong"]:
                if not kw_n or len(kw_n) < 2:
                    continue
                if self._hit(kw_n, kw, name_n, name_raw):
                    w = float(self.s.get("strong_weight", 10))
                    if len(kw_n) >= 4:
                        w += 2
                    strong_pts += w * self._w("strong", kw_n)
                    hits.append(kw)
            strong_pts = min(strong_pts, float(self.s.get("strong_cap", 30)))

            weak_pts = 0.0
            for kw_n, kw in c["weak"]:
                if kw_n and len(kw_n) >= 2 and self._hit(kw_n, kw, name_n, name_raw):
                    weak_pts += float(self.s.get("weak_weight", 3)) * self._w("weak", kw_n)
            weak_pts = min(weak_pts, float(self.s.get("weak_cap", 6)))

            origin_pts = 0.0
            if origin_n:
                for kw_n, kw in c["strong"]:
                    if kw_n and len(kw_n) >= 2 and kw_n in origin_n:
                        origin_pts = float(self.s.get("origin_bonus", 4))
                        hits.append(f"目录:{kw}")
                        break

            semantic = strong_pts + weak_pts + origin_pts
            pts = semantic * c["boost"]
            # 扩展名只在已有语义命中时加分，避免「.pdf 就归类」
            if semantic > 0 and ext in c["exts"]:
                pts += float(self.s.get("ext_bonus", 2))

            results.append((pts, c["priority"], c, hits))

        results.sort(key=lambda x: (-x[0], -x[1]))
        return results

    def assign(self, item):
        results = self.score(item)
        best = results[0]
        runner = results[1] if len(results) > 1 else (0.0, 0, None, [])
        gap = best[0] - runner[0]
        min_gap = float(self.s.get("min_confidence_gap", 6))

        # 没有任何语义命中：老实标为未分类，不按优先级硬塞
        if best[0] <= 0:
            return UNCLASSIFIED, "low", 0.0, None, 0.0, []

        if gap >= min_gap * 2:
            conf = "high"
        elif gap >= min_gap:
            conf = "medium"
        else:
            conf = "low"
        return best[2], conf, best[0], runner[2], gap, best[3]


def main():
    ap = argparse.ArgumentParser(description="给索引文件追加细分类标签")
    ap.add_argument("--index", required=True)
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--write", action="store_true", help="写回 index.json")
    ap.add_argument("--report", action="store_true", help="打印分布报告")
    ap.add_argument("--show-low", type=int, default=0, help="列出 N 个低置信度样例")
    args = ap.parse_args()

    index_path = os.path.abspath(os.path.expanduser(args.index))
    with open(index_path, encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.expanduser(args.taxonomy), encoding="utf-8") as f:
        tax = json.load(f)

    refiner = Refiner(tax)
    items = data.get("items", [])
    dist = collections.Counter()
    dist_group = collections.Counter()
    conf_cnt = collections.Counter()
    low_samples = []

    for it in items:
        c, conf, pts, second, gap, hits = refiner.assign(it)
        it["fine_id"] = c["id"]
        it["fine_name"] = c["name"]
        it["fine_group"] = c["group"]
        it["fine_conf"] = conf
        it["fine_score"] = round(pts, 1)
        if hits:
            it["fine_hits"] = hits[:4]
        dist[c["name"]] += 1
        dist_group[c["group"]] += 1
        conf_cnt[conf] += 1
        if conf == "low" and len(low_samples) < args.show_low:
            low_samples.append((it["name"], c["name"], second["name"] if second else "-", round(gap, 1)))

    data["taxonomy_version"] = tax.get("version", 0)
    data["refined_at"] = datetime.now().isoformat(timespec="seconds")
    data["fine_categories"] = [
        {"id": c["id"], "name": c["name"], "group": c["group"],
         "count": dist[c["name"]]}
        for c in tax["categories"]
    ]

    if args.write:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(index_path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, index_path)
        print(f"已写回 {index_path}")

    total = len(items) or 1
    print(f"\n共 {len(items)} 个文件，{len(tax['categories'])} 个细分类")
    print(f"置信度: high {conf_cnt['high']} ({conf_cnt['high']/total:.0%})  "
          f"medium {conf_cnt['medium']}  low {conf_cnt['low']}")
    if args.report:
        print("\n按分组：")
        for g, n in sorted(dist_group.items(), key=lambda x: -x[1]):
            print(f"  {g:14s} {n:5d}")
        print("\n按细分类（仅非空）：")
        for c in tax["categories"]:
            n = dist[c["name"]]
            if n:
                bar = "▇" * max(1, int(n / max(dist.values()) * 26))
                print(f"  {c['id']:3s} {c['name']:12s} {n:5d}  {bar}")
        empty = [c["name"] for c in tax["categories"] if not dist[c["name"]]]
        if empty:
            print(f"\n空分类（{len(empty)}）: {'、'.join(empty)}")
    if low_samples:
        print("\n低置信度样例：")
        for nm, a, b, gap in low_samples:
            print(f"  {nm[:44]:46s} {a} vs {b} (差 {gap})")


if __name__ == "__main__":
    main()
