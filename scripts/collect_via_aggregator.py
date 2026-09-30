#!/usr/bin/env python3
"""WAF 站兜底采集：走第三方聚合（Google News RSS / Bing News）。

适用对象：直接访问被 Cloudflare 企业级挑战或 Akamai 拒绝的站点。
思路：不直接打目标站，而是用聚合服务的 RSS ——
      聚合服务自己会去抓那些站，我们只消费它的结果。
      代价是只有标题+摘要（无正文），但足以做情报线索。

用法：
    python scripts/collect_via_aggregator.py

输出：合并进 out/news_overseas.json（与直采结果同一文件，按 url 去重）
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
}

# 被 WAF 拦的站点 → 用「站点名 + 行业关键词」去聚合服务检索
AGG_TARGETS = [
    ("Counterman",        "Counterman brake pad"),
    ("Tire Business",     "Tire Business brake"),
    ("Fleet Owner",       "Fleet Owner truck brake"),
    ("CCJ Digital",       "CCJ Digital brake"),
    ("Underhood Service", "Underhood Service brake"),
    ("Aftermarket News",  "Aftermarket News brake"),
    ("TMD Friction",      "TMD Friction brake pad"),
    ("UNECE WP.29",       "UNECE vehicle regulation braking"),
    ("Espacenet",         "brake pad friction patent"),
]

FRICTION_KW = [
    "friction", "brake", "pad", "lining", "tribology", "wear", "rotor", "disc",
    "drum", "caliper", "copper", "ceramic", "nao", "asbestos", "noise", "nvh",
    "regenerative", "r90", "emission", "dust", "formulation", "aftermarket",
    "oem", "automotive", "vehicle", "truck", "fleet", "regulation",
]


def translate(text: str, retry: int = 2) -> str:
    text = (text or "").strip()
    if not text or len(text) > 480:
        return text
    for _ in range(retry + 1):
        try:
            r = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "zh-CN",
                        "dt": "t", "q": text},
                headers=UA, timeout=12)
            if r.status_code == 200:
                data = r.json()
                seg = "".join(p[0] for p in data[0] if p and p[0])
                return seg.strip() or text
        except Exception:
            time.sleep(1.2)
    return text


def gnews_rss(query: str, limit: int = 15):
    """Google News RSS（免费、无需 key）。"""
    url = (f"https://news.google.com/rss/search?q={quote(query)}"
           f"&hl=en-US&gl=US&ceid=US:en")
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        root = ET.fromstring(r.content)
    except Exception as e:
        return [], f"{type(e).__name__}: {repr(e)[:70]}"

    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        if not t or not link:
            continue
        out.append({"title": t, "url": link, "published": pub})
        if len(out) >= limit:
            break
    return out, None


def bing_rss(query: str, limit: int = 15):
    """Bing News RSS —— Google News 不可达时的备用通道。"""
    url = (f"https://www.bing.com/news/search?q={quote(query)}"
           f"&format=RSS&count={limit}")
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        root = ET.fromstring(r.content)
    except Exception as e:
        return [], f"{type(e).__name__}: {repr(e)[:70]}"

    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not t or not link:
            continue
        out.append({"title": t, "url": link,
                    "published": (it.findtext("pubDate") or "").strip()})
        if len(out) >= limit:
            break
    return out, None


def fetch_aggregated(query: str, limit: int = 15):
    """先试 Google News，失败退 Bing News；都失败返回 ([], 原因)。"""
    items, err_g = gnews_rss(query, limit)
    if items:
        return items, None, "google-news-rss"
    items, err_b = bing_rss(query, limit)
    if items:
        return items, None, "bing-news-rss"
    return [], f"google={err_g}; bing={err_b}", None


def main():
    out_path = "out/news_overseas.json"
    os.makedirs("out", exist_ok=True)
    try:
        with open(out_path, encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        store = {}
    articles = {a["url"]: a for a in store.get("articles", []) if a.get("url")}

    stats, errors = [], []
    for name, query in AGG_TARGETS:
        items, err, via = fetch_aggregated(query)
        if err:
            errors.append({"source": f"{name}(聚合)", "error": err})
            print(f"  ⛔ {name:<20} {err[:80]}")
            continue
        kept = 0
        for it in items:
            low = it["title"].lower()
            if not any(k in low for k in FRICTION_KW):
                continue
            # 聚合结果标题末尾常带 " - 媒体名"，保留原样便于溯源
            rec = {
                "title": it["title"],
                "url": it["url"],
                "source": f"{name}(聚合)",
                "region": "海外",
                "tag": "聚合来源",
                "lang": "en",
                "via": via,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            zh = translate(it["title"])
            if zh and zh != it["title"]:
                rec["title_zh"] = zh
            articles[it["url"]] = rec
            kept += 1
        stats.append({"source": name, "via": via,
                      "fetched": len(items), "kept": kept})
        print(f"  ✅ {name:<20} [{via}] 取 {len(items):>3} 条，相关 {kept:>3} 条")

    store["articles"] = sorted(articles.values(),
                               key=lambda x: x["collected_at"], reverse=True)
    store["count"] = len(articles)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    store.setdefault("stats", []).extend(stats)
    store.setdefault("errors", []).extend(errors)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    print(f"\n合并后共 {len(articles)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
