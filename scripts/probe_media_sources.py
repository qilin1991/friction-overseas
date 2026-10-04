#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：验证 GitHub Actions 出口 IP 能否直连抓取这些被 Cloudflare 拦截的源站。

用法（在 Actions 里）：
    python3 scripts/probe_media_sources.py < urls.txt
或
    python3 scripts/probe_media_sources.py --urls "url1,url2"

输出：每条 URL 的 HTTP 状态、字节数、是否命中 Cloudflare 挑战、解析出的 <img> 数。
"""
import json
import os
import re
import sys

import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
CF_MARKERS = ("just a moment", "cf-browser-verification", "cf_chl_opt",
              "checking your browser", "enable javascript and cookies")


def probe(url, timeout=22):
    r = {"url": url, "status": 0, "bytes": 0, "cf": False, "imgs": 0, "err": ""}
    try:
        resp = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception as e:
        r["err"] = f"{type(e).__name__}: {e}"[:120]
        return r
    r["status"] = resp.status_code
    r["bytes"] = len(resp.content)
    low = resp.text[:4000].lower()
    r["cf"] = any(m in low for m in CF_MARKERS)
    if resp.status_code == 200 and not r["cf"]:
        try:
            soup = BeautifulSoup(resp.content, "html.parser")
            r["imgs"] = len(soup.find_all("img"))
        except Exception:
            pass
    return r


def load_urls():
    if "--urls" in sys.argv:
        i = sys.argv.index("--urls")
        return [u.strip() for u in sys.argv[i + 1].split(",") if u.strip()]
    path = "out/media_urls.txt"
    if not os.path.exists(path):
        path = "media_urls.txt"
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def main():
    urls = load_urls()
    if not urls:
        print("[probe] 没有 URL 输入")
        return
    print(f"[probe] 共 {len(urls)} 条")
    out, ok = [], 0
    for i, u in enumerate(urls, 1):
        r = probe(u)
        out.append(r)
        flag = "OK " if (r["status"] == 200 and not r["cf"]) else "BAD"
        if flag == "OK ":
            ok += 1
        print(f"[{i:>3}/{len(urls)}] {flag} {r['status']:>3} "
              f"{r['bytes']:>7}B imgs={r['imgs']:<3} cf={int(r['cf'])} "
              f"{u[:78]}", flush=True)
        if r["err"]:
            print(f"        err: {r['err']}", flush=True)
    print(f"\n[probe] 可直连 {ok}/{len(urls)}")
    os.makedirs("out", exist_ok=True)
    with open("out/media_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
