#!/usr/bin/env python3
"""探针四：候选新信源（2026-10-02 信源普查补充）可达性 + 聚合覆盖普查。

在 GitHub Actions / Azure 出口跑（与 probe_sources.py 同原理，但额外测 Bing News RSS
site:域名 覆盖度，直接给出「该源该走直连还是走聚合兜底」的推荐）。

输出：out/newsource_probe.json
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
RSS_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
}
BLOCK_MARKERS = ["just a moment", "access denied", "cf-browser",
                 "checking your browser", "attention required", "enable javascript"]

# (显示名, 列表页URL, 分类标签, Bing News RSS 检索词)
CANDIDATES = [
    ("Tribonet",                    "https://www.tribonet.org/",                 "摩擦学",   "tribology friction"),
    ("AZoM",                        "https://www.azom.com/",                     "材料科学", "brake friction material"),
    ("STLE",                        "https://www.stle.org/",                     "摩擦学协会","tribology"),
    ("American Ceramic Society",    "https://ceramics.org/news",                 "陶瓷",     "ceramic brake"),
    ("Truckinginfo",                "https://www.truckinginfo.com/",            "商用车",   "brake"),
    ("Trucknews",                   "https://www.trucknews.com/",               "商用车",   "brake"),
    ("Automotive World",            "https://www.automotiveworld.com/news/",    "行业媒体", "brake"),
    ("Just Auto",                   "https://www.just-auto.com/news/",          "行业媒体", "brake"),
    ("Railway Technology",          "https://www.railway-technology.com/",      "轨道",     "brake friction"),
    ("Progressive Railroading",     "https://www.progressiverailroading.com/",  "轨道",     "brake"),
    ("Hot Rod",                     "https://www.hotrod.com/",                   "改装技术", "brake pad"),
    ("AutoEvolution",               "https://www.autoevolution.com/news/",      "汽车技术", "brake pad"),
    ("The BRAKE Report",            "https://thebrakereport.com/",               "摩擦材料媒体","brake"),
    ("Tyrepress",                   "https://www.tyrepress.com/",               "轮胎",     "brake"),
    ("Brembo",                      "https://www.brembo.com/",                   "Tier1",    "brake"),
    ("Akebono",                     "https://www.akebono-brake.com/",           "Tier1",    "brake"),
    ("Ferodo",                      "https://www.ferodo.com/",                   "竞品",     "brake pad"),
    ("Textar",                      "https://www.textar.com/",                   "竞品",     "brake pad"),
    ("Wagner",                      "https://www.wagnerbrake.com/",              "竞品",     "brake"),
    ("Nisshinbo",                   "https://www.nisshinbo-brake.com/",          "竞品",     "brake"),
    ("Fras-le",                     "https://www.fras-le.com/",                  "竞品",     "brake"),
    ("Continental Aftermarket (ATE)","https://www.continental-aftermarket.com/","竞品",    "brake"),
    ("PR Newswire",                 "https://www.prnewswire.com/",               "通稿",     "brake pad"),
    ("Business Wire",               "https://www.businesswire.com/",             "通稿",     "brake"),
    ("Grand View Research",         "https://www.grandviewresearch.com/",        "市场",     "brake pad market"),
    ("International Copper Association","https://internationalcopper.org/",      "铜价",     "copper price"),
    ("Copper Development Association","https://www.copper.org/",                 "铜价",     "copper"),
]


def probe_direct(item):
    name, url, tag, kw = item
    try:
        r = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
        body = r.text
        blocked = any(m in body[:4000].lower() for m in BLOCK_MARKERS)
        hrefs = re.findall(r'href="([^"]+)"', body)
        arts = [h for h in hrefs
                if re.search(r"/(news|press|blog|article|20\d\d)/", h, re.I)]
        return {"name": name, "url": url, "tag": tag,
                "status": r.status_code, "blocked": blocked,
                "art_links": len(arts), "bytes": len(body),
                "final_url": r.url}
    except Exception as e:
        return {"name": name, "url": url, "tag": tag, "status": 0,
                "blocked": False, "art_links": 0, "bytes": 0,
                "error": type(e).__name__}


def probe_rss(item):
    name, url, tag, kw = item
    dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
    q = f"site:{dom} {kw}"
    api = ("https://www.bing.com/news/search?q=" + quote(q) +
           "&format=RSS&count=20")
    try:
        r = requests.get(api, headers=RSS_UA, timeout=20)
        if r.status_code != 200:
            return {"rss_status": r.status_code, "rss_hits": 0,
                    "rss_domain_hits": 0, "rss_samples": []}
        root = ET.fromstring(r.content)
        items = []
        for it in root.iter("item"):
            t = (it.findtext("title") or "").strip()
            l = (it.findtext("link") or "").strip()
            if t:
                items.append((t, l))
        dom_hits = sum(1 for t, l in items
                       if dom in (t + l).lower())
        samples = [t[:90] for t, l in items[:5]]
        return {"rss_status": 200, "rss_hits": len(items),
                "rss_domain_hits": dom_hits, "rss_samples": samples}
    except Exception as e:
        return {"rss_status": 0, "rss_hits": 0, "rss_domain_hits": 0,
                "rss_samples": [], "rss_error": type(e).__name__}


def recommend(r):
    direct_ok = (r["status"] == 200 and not r["blocked"] and r["art_links"] > 3)
    agg_ok = (r.get("rss_status") == 200 and r.get("rss_domain_hits", 0) > 0)
    if direct_ok:
        return "直连"
    if agg_ok:
        return "聚合"
    if r["status"] == 200 and not r["blocked"]:
        return "直连(文章少)"
    if r.get("rss_status") == 200:
        return "聚合(0命中)"
    return "不可用"


def main():
    os.makedirs("out", exist_ok=True)
    print(f"{'源':<26}{'直连':<10}{'RSS命中':<10}{'推荐'}")
    print("-" * 70)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        directs = list(ex.map(probe_direct, CANDIDATES))
        rsses = list(ex.map(probe_rss, CANDIDATES))
    merged = []
    for d, rs in zip(directs, rsses):
        d.update(rs)
        d["recommend"] = recommend(d)
        merged.append(d)
        dl = f"{d['status']}{' WAF' if d['blocked'] else ''}"
        print(f"{d['name']:<26}{dl:<10}{str(d.get('rss_domain_hits',0)):<10}{d['recommend']}",
              flush=True)
    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "total": len(merged),
        "results": merged,
    }
    with open("out/newsource_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    direct = [m for m in merged if m["recommend"].startswith("直连")]
    agg = [m for m in merged if m["recommend"].startswith("聚合")]
    print("\n=== 推荐直连 ===")
    for m in direct:
        print(f"  {m['name']}  ({m['url']})  art_links={m['art_links']}")
    print("=== 推荐聚合 ===")
    for m in agg:
        print(f"  {m['name']}  rss_domain_hits={m['rss_domain_hits']}")
    print("=== 不可用 ===")
    for m in merged:
        if not m["recommend"].startswith(("直连", "聚合")):
            print(f"  {m['name']}  -> {m['recommend']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
