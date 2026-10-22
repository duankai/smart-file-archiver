#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_api.py — 通过 GitHub REST API 推送本地目录到仓库（不依赖 git remote）

适用场景：本机 `git push` 走 HTTPS 时被代理拦截（HTTP2 framing / CONNECT 502），
但 api.github.com 可访问。此时用本脚本代替 git push。

特点：
  * 只上传内容真正变化的文件（本地 git-blob SHA1 与远端对比，精确判断，不看时间戳）
  * 自动处理新增 / 修改 / 删除，生成一条正常提交（作者信息可指定）
  * 不写入 token 到磁盘，token 从 --token 或环境变量 GITHUB_TOKEN 读取
  * --dry-run 先看会动哪些文件

用法：
  export GITHUB_TOKEN=github_pat_xxx
  python3 scripts/push_api.py --repo duankai/smart-file-archiver
  python3 scripts/push_api.py --repo duankai/smart-file-archiver -m "feat: xxx" --dry-run

依赖：仅标准库
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SKIP_DIRS = {".git", "__pycache__", ".idea", ".vscode", "node_modules", ".pytest_cache"}


def git_blob_sha(data: bytes) -> str:
    """复算 git 的 blob 对象 SHA1，用于与远端 tree 精确比对。"""
    head = b"blob %d\0" % len(data)
    return hashlib.sha1(head + data).hexdigest()


class GH:
    def __init__(self, token: str) -> None:
        self.token = token

    def call(self, method: str, path: str, payload: dict | None = None):
        url = path if path.startswith("http") else API + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            raise SystemExit(f"[错误] {method} {path} -> HTTP {e.code}\n{detail}")


def load_ignore(root: str) -> list[str]:
    """读取 .gitignore 的简单模式（支持 # 注释、目录结尾 /、* 通配）。"""
    p = os.path.join(root, ".gitignore")
    pats: list[str] = []
    if not os.path.exists(p):
        return pats
    for line in open(p, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        pats.append(s.rstrip("/"))
    return pats


def ignored(rel: str, pats: list[str]) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    for pat in pats:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*"):
            return True
        if any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
    return False


def collect(root: str) -> dict[str, tuple[str, bytes]]:
    """返回 {相对路径: (blob_sha, 内容)}"""
    pats = load_ignore(root)
    out: dict[str, tuple[str, bytes]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in {".DS_Store"}:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if ignored(rel, pats):
                continue
            try:
                data = open(full, "rb").read()
            except OSError as e:
                print(f"  [跳过] {rel}: {e}")
                continue
            out[rel] = (git_blob_sha(data), data)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="用 GitHub API 推送目录到仓库")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--dir", default=".", help="本地目录，默认当前目录")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("-m", "--message", default="")
    ap.add_argument("--author-name", default="")
    ap.add_argument("--author-email", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-delete", action="store_true", help="同步删除远端多余文件")
    args = ap.parse_args()

    if not args.token:
        raise SystemExit("[错误] 缺少 token：--token 或环境变量 GITHUB_TOKEN")

    root = os.path.abspath(args.dir)
    gh = GH(args.token)

    print(f"[1/5] 读取远端 {args.repo}@{args.branch}")
    ref = gh.call("GET", f"/repos/{args.repo}/git/ref/heads/{args.branch}")
    head_sha = ref["object"]["sha"]
    commit = gh.call("GET", f"/repos/{args.repo}/git/commits/{head_sha}")
    base_tree = commit["tree"]["sha"]
    tree = gh.call("GET", f"/repos/{args.repo}/git/trees/{base_tree}?recursive=1")
    remote = {
        t["path"]: t["sha"] for t in tree.get("tree", []) if t.get("type") == "blob"
    }
    print(f"      远端 HEAD {head_sha[:7]}，{len(remote)} 个文件")

    print(f"[2/5] 扫描本地 {root}")
    local = collect(root)
    print(f"      {len(local)} 个文件（已按 .gitignore 过滤）")

    added = sorted(p for p in local if p not in remote)
    modified = sorted(p for p in local if p in remote and local[p][0] != remote[p])
    deleted = sorted(p for p in remote if p not in local)

    print("[3/5] 差异")
    for p in added:
        print(f"      + {p}")
    for p in modified:
        print(f"      ~ {p}")
    for p in deleted:
        print(f"      - {p}  {'（将删除）' if args.allow_delete else '（保留，加 --allow-delete 才删）'}")

    if not (added or modified or (deleted and args.allow_delete)):
        print("\n已是最新，无需提交。")
        return

    if args.dry_run:
        print("\n[dry-run] 未做任何改动。")
        return

    print("[4/5] 上传变更")
    entries = []
    for p in added + modified:
        sha, data = local[p]
        blob = gh.call(
            "POST",
            f"/repos/{args.repo}/git/blobs",
            {"content": base64.b64encode(data).decode(), "encoding": "base64"},
        )
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"      ✓ {p}")
    if args.allow_delete:
        for p in deleted:
            entries.append(
                {"path": p, "mode": "100644", "type": "blob", "sha": None}
            )
            print(f"      ✗ {p}")

    new_tree = gh.call(
        "POST",
        f"/repos/{args.repo}/git/trees",
        {"base_tree": base_tree, "tree": entries},
    )

    msg = args.message or f"chore: 同步 {len(added) + len(modified)} 个文件"
    payload = {"message": msg, "tree": new_tree["sha"], "parents": [head_sha]}
    if args.author_name:
        payload["author"] = {
            "name": args.author_name,
            "email": args.author_email or f"{args.author_name}@users.noreply.github.com",
        }
    new_commit = gh.call("POST", f"/repos/{args.repo}/git/commits", payload)

    print("[5/5] 更新分支引用")
    gh.call(
        "PATCH",
        f"/repos/{args.repo}/git/refs/heads/{args.branch}",
        {"sha": new_commit["sha"], "force": False},
    )
    print(f"\n完成：{args.repo}@{args.branch} <- {new_commit['sha'][:7]}  {msg}")
    print(f"https://github.com/{args.repo}/commit/{new_commit['sha']}")


if __name__ == "__main__":
    main()
