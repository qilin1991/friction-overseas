#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""epmc_lookup.py —— 在 GitHub Actions（Azure IP）上查 Europe PMC 的 OA 全文 PDF URL。

为什么在 Actions 跑：VPS 直连 / WARP 代理 / fetch-relay 都被 Europe PMC 的
Cloudflare 拦（Just a moment...），只有 Azure runner 这种干净 IP 能查到。

输入：环境变量 DOIS = JSON 数组（DOI 列表）
输出：data/epmc_result.json = {doi: {"title":.., "pdfs":[url,...]}}
只收 availability=="Open Access" 且 documentStyle=="pdf" 的 URL。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

EPMC = "https://www.europepmc.org/webservices/rest/search"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")


def query(doi):
    q = "%s?query=%s&format=json&resultType=core" % (EPMC, urllib.parse.quote(doi))
    try:
        req = urllib.request.Request(
            q, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except Exception as e:
        return None, str(e)[:80]
    results = d.get("resultList", {}).get("result", [])
    out = []
    for x in results:
        for u in x.get("fullTextUrlList", {}).get("fullTextUrl", []):
            if (u.get("availability") == "Open Access"
                    and u.get("documentStyle") == "pdf" and u.get("url")):
                out.append(u["url"])
        if out:
            break
    title = (results[0].get("title") if results else None)
    return out, title


def norm_doi(s):
    s = (s or "").strip()
    for p in ("https://doi.org/", "http://doi.org/",
              "https://dx.doi.org/", "doi:"):
        if s.lower().startswith(p):
            s = s[len(p):]
    return s.strip().lower()


def main():
    raw = os.environ.get("DOIS", "[]")
    # 优先用 VPS 提交的待查清单（避免 workflow_dispatch 字符串输入的长度上限）
    if os.path.exists("data/epmc_pending.json"):
        try:
            raw = open("data/epmc_pending.json", encoding="utf-8").read()
        except Exception:
            pass
    try:
        dois = [norm_doi(x) for x in json.loads(raw) if norm_doi(x)]
    except Exception:
        dois = []
    print("查询 %d 个 DOI" % len(dois))
    res = {}
    for i, doi in enumerate(dois):
        pdfs, title = query(doi)
        if pdfs:
            res[doi] = {"title": title, "pdfs": pdfs}
        if (i + 1) % 50 == 0 or (i + 1) == len(dois):
            print("  %d/%d 命中 OA PDF %d" % (i + 1, len(dois), len(res)),
                  flush=True)
        time.sleep(0.05)
    os.makedirs("data", exist_ok=True)
    with open("data/epmc_result.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("DONE 查询 %d 命中 OA PDF %d" % (len(dois), len(res)), flush=True)


if __name__ == "__main__":
    main()
