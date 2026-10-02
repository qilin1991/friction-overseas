#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发现信源：用 Bing（Azure 出口）搜索「摩擦材料 / 制动器」主题，
收集返回的域名，过滤门户/社交/通用站，筛出行业信源。
输出：out/discover_report.txt（域名频率）、out/discover_sources.jsonl（明细）
"""
import json, os, re, base64, requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleKeeper/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TOUT = 25
os.makedirs("out", exist_ok=True)

# 摩擦材料 / 制动器 主题关键词
QUERIES = [
    "friction material brake",
    "brake pad friction material",
    "automotive friction materials manufacturer",
    "NAO brake pad non asbestos organic",
    "brake friction industry news",
    "friction technology braking",
    "dry brake pad formulation",
    "brake pad wear debris copper free",
    "friction materials standards institute FMSI",
    "brake noise vibration harshness NVH",
    "ceramic brake pad formulation",
    "brake lining friction",
    "tribology friction brake",
    "brake pad dynamometer testing",
    "low metallic brake friction",
    "brake pad aftermarket",
    "rail friction material",
    "clutch friction material",
]

BING_WEB = "https://www.bing.com/search?q={q}&setmkt=en-US&cc=US&count=50"
BING_NEWS = "https://www.bing.com/news/search?q={q}&setmkt=en-US"

BLOCK = ("bing.com", "google.com", "googleusercontent", "yahoo.com", "yandex.com",
         "duckduckgo.com", "startpage.com", "mojeek.com", "aol.com", "ask.com", "searx",
         "wikipedia.org", "youtube.com", "twitter.com", "facebook.com", "linkedin.com",
         "microsoft.com", "apple.com", "amazon.com", "reddit.com", "pinterest.com",
         "instagram.com", "tiktok.com", "msn.com", "bingj", "goo.gl", "bit.ly",
         "googleapis", "gstatic", "cloudfront", "w3.org", "aka.ms")

def dom(u):
    try:
        n = urlsplit(u).netloc.lower()
        if n.startswith("www."):
            n = n[4:]
        return n
    except Exception:
        return ""

def extract_bing_web(html):
    out = []
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a") or li.select_one("a")
        if a and a.get("href"):
            out.append(a["href"])
    for a in soup.select("a[href*='bing.com/ck/a']"):
        m = re.search(r"u=a1([^&]+)", a.get("href", ""))
        if m:
            try:
                out.append(base64.urlsafe_b64decode(m.group(1) + "==").decode("utf-8", "ignore"))
            except Exception:
                pass
    return out

def extract_bing_news(html):
    out = []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href^='http']"):
        h = a.get("href", "")
        if "bing.com" in h.lower():
            continue
        out.append(h)
    return out

domains = {}
samples = []
for q in QUERIES:
    for kind, url, fn in (("web", BING_WEB, extract_bing_web), ("news", BING_NEWS, extract_bing_news)):
        try:
            r = requests.get(url.format(q=requests.utils.quote(q)),
                             headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                             timeout=TOUT, allow_redirects=True)
            st = r.status_code
        except Exception as e:
            print("ERR", kind, q, type(e).__name__)
            continue
        if st != 200:
            print("HTTP", st, kind, q)
            continue
        for u in fn(r.text):
            d = dom(u)
            if not d or any(b in d for b in BLOCK):
                continue
            domains[d] = domains.get(d, 0) + 1
            if len(samples) < 400:
                samples.append({"q": q, "kind": kind, "d": d, "u": u})

with open(os.path.join("out", "discover_sources.jsonl"), "w", encoding="utf-8") as f:
    for s in samples:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")

ranked = sorted(domains.items(), key=lambda x: -x[1])
with open(os.path.join("out", "discover_report.txt"), "w", encoding="utf-8") as f:
    f.write("queries=%d unique_domains=%d\n\n" % (len(QUERIES), len(domains)))
    for d, n in ranked:
        f.write("  %3d  %s\n" % (n, d))

print("queries=%d unique_domains=%d" % (len(QUERIES), len(domains)))
print("TOP30:")
for d, n in ranked[:30]:
    print("  %3d  %s" % (n, d))
