#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 — 本地文件检索服务（仅标准库，零依赖）。

用法:
    serve.py --index ~/Desktop/文件归档/index.json --open-browser
    serve.py --index index.json --port 8765

接口:
    GET  /                     检索界面
    GET  /api/search?q=&cat=&ext=&conf=&source=&sort=&limit=
    GET  /api/meta             分类与统计（轻量）
    GET  /api/stats            库存统计（分类/格式/月度分布/缺失文件，30s 缓存）
    GET  /api/preview?path=    页面内预览（图片/PDF/文本，白名单校验）
    GET  /api/history          最近打开记录
    POST /api/open   {"path": "..."}     用 WPS/Office 打开（记录历史）
    POST /api/reveal {"path": "..."}     在 Finder 中显示

安全:
    - 预览/打开/定位只允许 index.json 中登记过的路径（防目录穿越）
    - 服务仅监听 127.0.0.1，不对外网暴露
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_template() -> str:
    """界面模板可能在技能目录，也可能随归档区一起分发。"""
    for cand in (
        os.path.join(HERE, "assets", "index_template.html"),
        os.path.join(os.path.dirname(HERE), "assets", "index_template.html"),
        os.path.join(HERE, "index_template.html"),
    ):
        if os.path.exists(cand):
            return cand
    return ""


TEMPLATE = _find_template()

# 按扩展名选择偏好的应用，WPS 优先
APP_PREF = {
    "doc": ["/Applications/wpsoffice.app", "/Applications/Microsoft Word.app"],
    "docx": ["/Applications/wpsoffice.app", "/Applications/Microsoft Word.app"],
    "wps": ["/Applications/wpsoffice.app", "/Applications/Microsoft Word.app"],
    "xls": ["/Applications/wpsoffice.app", "/Applications/Microsoft Excel.app"],
    "xlsx": ["/Applications/wpsoffice.app", "/Applications/Microsoft Excel.app"],
    "csv": ["/Applications/wpsoffice.app", "/Applications/Microsoft Excel.app"],
    "ppt": ["/Applications/wpsoffice.app", "/Applications/Microsoft PowerPoint.app"],
    "pptx": ["/Applications/wpsoffice.app", "/Applications/Microsoft PowerPoint.app"],
    "pdf": ["/Applications/wpsoffice.app", "/System/Applications/Preview.app"],
    "txt": ["/Applications/wpsoffice.app", "/System/Applications/TextEdit.app"],
    "md": ["/Applications/wpsoffice.app", "/System/Applications/TextEdit.app"],
}

# 页面内预览能力：扩展名 -> (mime, 单文件大小上限字节)
PREVIEWABLE = {
    "png": ("image/png", 25 << 20), "jpg": ("image/jpeg", 25 << 20),
    "jpeg": ("image/jpeg", 25 << 20), "gif": ("image/gif", 25 << 20),
    "webp": ("image/webp", 25 << 20), "bmp": ("image/bmp", 25 << 20),
    "svg": ("image/svg+xml", 5 << 20),
    "pdf": ("application/pdf", 40 << 20),
}
TEXT_PREVIEW_EXTS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "log", "tex",
    "py", "js", "ts", "html", "htm", "css", "yaml", "yml", "xml", "sh",
}
TEXT_PREVIEW_CAP = 2 << 20  # 文本预览最多读 2MB

MIME_TEXT = "text/plain; charset=utf-8"

STATE = {
    "index": None, "allowed": set(), "root": "",
    "index_path": "", "history_path": "", "history_lock": threading.Lock(),
    "stats_cache": {"ts": 0.0, "data": None},
}
STATS_TTL = 30.0
HISTORY_MAX = 300


def load_index(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    STATE["index"] = data
    STATE["root"] = data.get("archive_root", "")
    STATE["allowed"] = {item["path"] for item in data.get("items", [])}
    STATE["index_path"] = os.path.abspath(path)
    STATE["history_path"] = os.path.join(os.path.dirname(STATE["index_path"]), "history.json")
    return data


def pick_app(path: str) -> str:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    for cand in APP_PREF.get(ext, []):
        if os.path.exists(cand):
            return cand
    return ""


def search(q: str, cat: str, ext: str, conf: str, source: str, sort: str,
           fine: str, recent: int, limit: int):
    items = STATE["index"].get("items", [])
    terms = [t for t in q.lower().split() if t]
    cutoff = ""
    if recent > 0:
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - recent * 86400))

    scored = []
    for it in items:
        if cat and it.get("category") != cat:
            continue
        if fine and it.get("fine_name") != fine:
            continue
        if ext and it.get("ext", "").lower() != ext.lower():
            continue
        if conf and it.get("confidence") != conf:
            continue
        if source and it.get("source_domain") != source:
            continue
        if cutoff and (it.get("mtime") or "")[:10] < cutoff:
            continue

        if terms:
            name_l = it["name"].lower()
            hay = " ".join([name_l, it.get("category", "").lower(),
                            it.get("origin", "").lower()])
            if not all(t in hay for t in terms):
                continue

            # 排序权重：全等 > 前缀 > 子串 > 仅路径命中
            full = " ".join(terms)
            if name_l == full:
                base = 1000
            elif name_l.startswith(terms[0]):
                base = 500
            elif all(t in name_l for t in terms):
                base = 200
            else:
                base = 50
        else:
            base = 0

        scored.append((base, it))

    if sort == "mtime" or (not sort and not terms):
        scored.sort(key=lambda x: x[1].get("mtime", ""), reverse=True)
    elif sort == "size":
        scored.sort(key=lambda x: -x[1].get("size", 0))
    elif sort == "relevance":
        scored.sort(key=lambda x: (-x[0], x[1].get("mtime", "")), reverse=False)
    else:  # 默认：有查询时相关度优先，否则新文件在前
        scored.sort(key=lambda x: (-x[0], x[1].get("mtime", "")), reverse=False)

    out = [s[1] for s in scored[:limit]]
    return {"total": len(scored), "returned": len(out), "items": out}


def compute_stats():
    data = STATE["index"]
    cats, exts, months = {}, {}, {}
    total_size = 0
    missing, missing_names = 0, []
    for it in data.get("items", []):
        c = it.get("category", "未知")
        e = it.get("ext") or "(无)"
        sz = it.get("size", 0)
        total_size += sz
        cats.setdefault(c, [0, 0])
        cats[c][0] += 1
        cats[c][1] += sz
        exts.setdefault(e, [0, 0])
        exts[e][0] += 1
        exts[e][1] += sz
        m = (it.get("mtime") or "")[:7]
        if m:
            months[m] = months.get(m, 0) + 1
        p = it.get("path", "")
        if p and not os.path.exists(p):
            missing += 1
            if len(missing_names) < 8:
                missing_names.append(it.get("name", ""))
    return {
        "count": data.get("count", 0),
        "total_size": total_size,
        "generated_at": data.get("generated_at", ""),
        "categories": sorted(([k, v[0], v[1]] for k, v in cats.items()),
                             key=lambda x: -x[1]),
        "exts": sorted(([k, v[0], v[1]] for k, v in exts.items()),
                       key=lambda x: -x[1])[:15],
        "months": sorted(months.items()),
        "missing": missing,
        "missing_names": missing_names,
    }


def get_stats():
    cache = STATE["stats_cache"]
    now = time.time()
    if cache["data"] is None or now - cache["ts"] > STATS_TTL:
        cache["data"] = compute_stats()
        cache["ts"] = now
    return cache["data"]


def load_history():
    try:
        with open(STATE["history_path"], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def record_history(item):
    """记录打开动作，按路径去重、保留最近 HISTORY_MAX 条。"""
    entry = {
        "path": item.get("path", ""),
        "name": item.get("name", ""),
        "category": item.get("category", ""),
        "size": item.get("size", 0),
        "ext": item.get("ext", ""),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with STATE["history_lock"]:
        hist = [h for h in load_history() if h.get("path") != entry["path"]]
        hist.insert(0, entry)
        try:
            with open(STATE["history_path"], "w", encoding="utf-8") as f:
                json.dump(hist[:HISTORY_MAX], f, ensure_ascii=False, indent=1)
        except OSError:
            pass


def preview_info(path: str):
    """返回 (mime, size_cap) 或 None 表示不支持预览。"""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext in PREVIEWABLE:
        return PREVIEWABLE[ext]
    if ext in TEXT_PREVIEW_EXTS:
        return (MIME_TEXT, TEXT_PREVIEW_CAP)
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartFileArchiver/2.0"

    def log_message(self, fmt, *args):
        pass  # 静默，避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            try:
                with open(TEMPLATE, encoding="utf-8") as f:
                    html = f.read()
            except OSError:
                html = "<h1>缺少界面模板 assets/index_template.html</h1>"
            self._send(200, html, "text/html; charset=utf-8")
            return

        if path == "/api/meta":
            data = STATE["index"]
            cats, exts, srcs = {}, {}, {}
            fines, fgroups, fgroup_of = {}, {}, {}
            for it in data.get("items", []):
                cats[it["category"]] = cats.get(it["category"], 0) + 1
                e = it.get("ext") or "(无)"
                exts[e] = exts.get(e, 0) + 1
                s = it.get("source_domain") or "custom"
                srcs[s] = srcs.get(s, 0) + 1
                f = it.get("fine_name")
                if f:
                    fines[f] = fines.get(f, 0) + 1
                    fgroup_of[f] = it.get("fine_group") or ""
                    g = it.get("fine_group") or ""
                    fgroups[g] = fgroups.get(g, 0) + 1
            self._json({
                "count": data.get("count", 0),
                "generated_at": data.get("generated_at", ""),
                "archive_root": data.get("archive_root", ""),
                "taxonomy_version": data.get("taxonomy_version", 0),
                "categories": sorted(cats.items(), key=lambda x: -x[1]),
                "exts": sorted(exts.items(), key=lambda x: -x[1])[:20],
                "sources": sorted(srcs.items(), key=lambda x: -x[1]),
                "fines": [[k, v, fgroup_of.get(k, "")] for k, v in
                          sorted(fines.items(), key=lambda x: -x[1])],
                "fine_groups": sorted(fgroups.items(), key=lambda x: -x[1]),
            })
            return

        if path == "/api/stats":
            self._json(get_stats())
            return

        if path == "/api/history":
            self._json({"items": load_history()[:40]})
            return

        if path == "/api/search":
            q = qs.get("q", [""])[0]
            cat = qs.get("cat", [""])[0]
            ext = qs.get("ext", [""])[0]
            conf = qs.get("conf", [""])[0]
            source = qs.get("source", [""])[0]
            sort = qs.get("sort", [""])[0]
            fine = qs.get("fine", [""])[0]
            try:
                recent = int(qs.get("recent", ["0"])[0])
            except ValueError:
                recent = 0
            try:
                limit = min(int(qs.get("limit", ["200"])[0]), 1000)
            except ValueError:
                limit = 200
            self._json(search(q, cat, ext, conf, source, sort, fine, recent, limit))
            return

        if path == "/api/preview":
            target = qs.get("path", [""])[0]
            real = os.path.realpath(target)
            if real not in STATE["allowed"]:
                self._json({"ok": False, "error": "路径不在索引白名单内"}, 403)
                return
            if not os.path.exists(real):
                self._json({"ok": False, "error": "文件不存在"}, 404)
                return
            info = preview_info(real)
            if not info:
                self._json({"ok": False, "error": "该格式不支持页面内预览"}, 415)
                return
            mime, cap = info
            if os.path.getsize(real) > cap:
                self._json({"ok": False, "error": "文件过大，请下载后用应用打开"}, 413)
                return
            try:
                with open(real, "rb") as f:
                    body = f.read(cap)
            except OSError as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._send(200, body, mime)
            return

        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json({"ok": False, "error": "请求体解析失败"}, 400)
            return

        target = body.get("path", "")
        if not target:
            self._json({"ok": False, "error": "缺少 path"}, 400)
            return

        # 白名单校验：必须是索引里登记过的绝对路径
        real = os.path.realpath(target)
        if real not in STATE["allowed"]:
            self._json({"ok": False, "error": "路径不在索引白名单内"}, 403)
            return
        if not os.path.exists(real):
            self._json({"ok": False, "error": "文件不存在，可能已被移动或删除"}, 404)
            return

        # 从索引取完整条目用于历史记录
        item = next((it for it in STATE["index"].get("items", [])
                     if it.get("path") == real), {})

        if parsed.path == "/api/reveal":
            subprocess.run(["open", "-R", real], check=False)
            record_history(item or {"path": real, "name": os.path.basename(real)})
            self._json({"ok": True, "action": "reveal"})
            return

        if parsed.path == "/api/open":
            app = pick_app(real)
            try:
                if app:
                    subprocess.run(["open", "-a", app, real], check=False)
                else:
                    subprocess.run(["open", real], check=False)
                record_history(item or {"path": real, "name": os.path.basename(real)})
                self._json({"ok": True, "app": os.path.basename(app) or "系统默认"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        self._json({"ok": False, "error": "未知接口"}, 404)


def find_port(start: int) -> int:
    import socket
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    sys.exit("找不到可用端口")


def main():
    ap = argparse.ArgumentParser(description="本地文件检索服务")
    ap.add_argument("--index", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open-browser", action="store_true")
    args = ap.parse_args()

    index_path = os.path.abspath(os.path.expanduser(args.index))
    if not os.path.exists(index_path):
        sys.exit(f"[错误] 索引不存在: {index_path}")

    data = load_index(index_path)
    port = find_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    print("=" * 56)
    print("  文件检索工具已启动 (v2)")
    print(f"  索引: {index_path}")
    print(f"  文件: {data.get('count', 0)} 条")
    print(f"  地址: {url}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)

    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
