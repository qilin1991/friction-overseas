#!/usr/bin/env python3
"""探针三：候选信源可达性普查（在 Actions 的 Azure IP 上跑）。

沙箱 / 国内 VPS 出口 IP 被大面积 WAF 封禁，判断不准。
真正可信的结论必须在 GitHub Actions 里跑出来。

输出：out/source_scan.json
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import time

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BLOCK_MARKERS = ["just a moment", "access denied", "cf-browser",
                 "checking your browser", "attention required"]

# 候选源：摩擦材料 / 制动器 行业上下游
CANDIDATES = [
    # ── 摩擦材料专业媒体 ──
    ("The BRAKE Report",      "https://thebrakereport.com/",                        "摩擦材料媒体"),
    ("Brake & Front End",     "https://www.brakeandfrontend.com/",                  "售后技术"),
    ("ImportCar",             "https://www.import-car.com/",                        "售后技术"),
    ("Tomorrow's Technician", "https://www.tomorrowstechnician.com/",               "售后技术"),
    ("Ratchet+Wrench",        "https://www.ratchetandwrench.com/",                  "售后技术"),
    ("Tyrepress",             "https://www.tyrepress.com/",                         "轮胎/排放"),
    # ── 制动器 Tier1 ──
    ("Knorr-Bremse",          "https://www.knorr-bremse.com/en/company/press/press-releases/", "制动Tier1"),
    ("Haldex",                "https://www.haldex.com/en/news/",                     "制动Tier1"),
    ("Miba AG",               "https://www.miba.com/en/news",                        "摩擦材料"),
    ("TMD Friction",          "https://www.tmdfriction.com/en/news/",                "摩擦材料"),
    # ── 标准 / 协会 ──
    ("ISO",                   "https://www.iso.org/insights",                       "标准"),
    ("ASTM",                  "https://www.astm.org/newsroom.html",                  "标准"),
    ("OICA",                  "https://www.oica.net/category/production-statistics/", "协会"),
    ("MEMA",                  "https://www.mema.org/press-releases/",                "协会"),
    ("Auto Care Association", "https://www.autocare.org/news",                       "协会"),
    # ── 综合行业媒体 ──
    ("Automotive World",      "https://www.automotiveworld.com/news/",               "行业媒体"),
    ("Just Auto",             "https://www.just-auto.com/news/",                     "行业媒体"),
    ("Automotive News",       "https://www.autonews.com/",                           "行业媒体"),
    ("Repairer Driven News",  "https://www.repairerdrivennews.com/",                 "售后"),
    ("BodyShop Business",     "https://www.bodyshopbusiness.com/",                   "售后"),
]


def probe(item):
    name, url, tag = item
    try:
        r = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
        body = r.text
        blocked = any(m in body[:4000].lower() for m in BLOCK_MARKERS)
        hrefs = re.findall(r'href="([^"]+)"', body)
        arts = [h for h in hrefs
                if re.search(r"/(news|press|blog|article|20\d\d)/", h, re.I)]
        return {
            "name": name, "url": url, "tag": tag,
            "status": r.status_code, "blocked": blocked,
            "art_links": len(arts), "bytes": len(body),
            "final_url": r.url,
        }
    except Exception as e:
        return {"name": name, "url": url, "tag": tag, "status": 0,
                "blocked": False, "art_links": 0, "bytes": 0,
                "error": f"{type(e).__name__}"}


def verdict_of(r):
    if r["status"] == 0:
        return f"⛔ {r.get('error','ERR')}"
    if r["blocked"]:
        return "⛔ WAF"
    if r["status"] == 200:
        return "✅ 可直连" if r["art_links"] > 3 else "🔶 可连·文章少"
    return f"⛔ HTTP {r['status']}"


def main():
    os.makedirs("out", exist_ok=True)
    results = []

    print(f"{'站点':<24}{'状态':<8}{'文章链接':<10}{'判定'}")
    print("-" * 68)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(probe, CANDIDATES):
            r["verdict"] = verdict_of(r)
            results.append(r)
            print(f"{r['name']:<24}{str(r['status']):<8}{str(r['art_links']):<10}{r['verdict']}",
                  flush=True)

    ok = [r for r in results if r["status"] == 200 and not r["blocked"]]
    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "total": len(results),
        "reachable": len(ok),
        "summary": f"{len(ok)}/{len(results)} 可直连",
        "results": results,
    }
    with open("out/source_scan.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 68)
    print(f"可直连 {len(ok)}/{len(results)}")
    for r in ok:
        print(f"  ✅ {r['name']:<24} {r['url']}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
