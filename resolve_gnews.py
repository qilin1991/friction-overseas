#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补搜信源（第三轮）：针对第二轮漏网的制造商官网 + 市场报告站，用
「official website」提升官网命中、「market report」命中专门市场站。
收集返回域名去重，过滤噪音，按类别预标注。
输出：out/discover_report.txt / out/discover_domains.json / out/discover_sources.jsonl
"""
import json, os, re, base64, requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TOUT = 25
os.makedirs("out", exist_ok=True)

# ---------- 第三轮补搜关键词 ----------
QUERIES = [
    # 制造商官网（official website 提升官网命中，避开聚合页）
    "Ferodo brake pad official website",
    "Textar brake pad official website",
    "Pagid brake pad official website",
    "Wagner brake pad official website",
    "Centric brake pad official website",
    "Hawk Performance brake pad official website",
    "Federal-Mogul brake pad official website",
    "Mintex brake pad official website",
    "Jurid brake pad official website",
    "ICER brake pad official website",
    "Sangsin brake pad official website",
    "Mak brake pad official website",
    "Bosch brake pad official website",
    "Carlisle brake friction official website",
    # 市场报告站补搜
    "brake pad market report CAGR",
    "brake friction materials market size forecast",
    "automotive brake pad aftermarket market report",
    "brake lining market report global",
]

BING_WEB = "https://www.bing.com/search?q={q}&setmkt=en-US&cc=US&count=50"
BING_NEWS = "https://www.bing.com/news/search?q={q}&setmkt=en-US"

BLOCK = ("bing.com", "google.com", "googleusercontent", "yahoo.com", "yandex.com",
         "duckduckgo.com", "startpage.com", "mojeek.com", "aol.com", "ask.com", "searx",
         "wikipedia.org", "youtube.com", "twitter.com", "facebook.com", "linkedin.com",
         "microsoft.com", "apple.com", "amazon.com", "reddit.com", "pinterest.com",
         "instagram.com", "tiktok.com", "msn.com", "bingj", "goo.gl", "bit.ly",
         "googleapis", "gstatic", "cloudfront", "w3.org", "aka.ms", "wikihow.com",
         "quora.com", "medium.com", "github.com", "stackoverflow.com")

STATIC = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".json",
          ".woff", ".woff2", ".ttf", ".xml", ".pdf")

MANUF = ("brembo", "bosch", "ferodo", "textar", "carlisle", "tmd-friction", "tmd",
         "bendix", "akebono", "nisshinbo", "fras-le", "frasle", "federal-mogul",
         "fedmogul", "matholdings", "mat-holdings", "sangsin", "continental",
         "ate", "pagid", "wagner", "raybestos", "ebcbrakes", "ebc", "hawkperformance",
         "hawk", "wilwood", "centric", "powerstop", "sbs", "galfer", "napaonline",
         "napa", "oreillyauto", "oreilly", "brakepartsinc", "icer", "jurid", "mintex",
         "mak", "fdb", "tarox", "rockauto", "advanceautoparts", "autozone",
         "partsgeek", "carparts")
ACADEMIC = ("sciencedirect", "springer", "mdpi", "frontiersin", "tribology", "stle",
            "sae", "researchgate", "sciencedaily", "eurekalert", "nature", "wiley",
            "tandfonline", "asm", "ceramics", "azom", "nasa", "ieee", "osti")
GOVREG = ("epa.gov", "transport", "unece", "europa.eu", "gov", "nhtsa", "ec.europa",
          "defra", "europarl", "bar.ca.gov")
MEDIA = ("truckinginfo", "fleetowner", "ccjdigital", "trucknews", "automotiveworld",
         "just-auto", "hotrod", "autoevolution", "motortrend", "autoguide", "thedrive",
         "autoblog", "carthrottle", "evo.co", "zigwheels", "progressiverailroading",
         "railway-technology", "prnewswire", "businesswire", "wardsauto", "aftermarket",
         "freightwaves", "fleetequipment", "tirebusiness", "designnews", "jalopnik",
         "caranddriver", "topspeed", "popularmechanics")
MARKET = ("marketresearch", "grandviewresearch", "fortunebusinessinsights",
          "researchandmarkets", "marketsandmarkets", "persistencemarketresearch",
          "cognitivemarketresearch", "dataintelo", "verifiedmarketreports",
          "thebrainyinsights", "maximizemarketresearch", "straitsresearch",
          "mordorintelligence", "precedenceresearch", "futuremarketinsights",
          "qyresearch", "towardsautomotive", "gminsights", "expertmarketresearch")

def classify(d):
    if any(m in d for m in MANUF):
        return "MANUF"
    if any(a in d for a in ACADEMIC):
        return "ACADEMIC"
    if any(g in d for g in GOVREG):
        return "GOVREG"
    if any(m in d for m in MEDIA):
        return "MEDIA"
    if any(m in d for m in MARKET):
        return "MARKET"
    return "OTHER"

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
        if "bing.com" in h.lower() or h.lower().endswith(STATIC):
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
            if d in domains:
                domains[d]["n"] += 1
            else:
                domains[d] = {"n": 1, "sample": u}
            if len(samples) < 600:
                samples.append({"q": q, "kind": kind, "d": d, "u": u})

with open(os.path.join("out", "discover_sources.jsonl"), "w", encoding="utf-8") as f:
    for s in samples:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")

ranked = sorted(domains.items(), key=lambda x: -x[1]["n"])
alld = {d: {"n": v["n"], "sample": v["sample"], "cls": classify(d)} for d, v in domains.items()}
with open(os.path.join("out", "discover_domains.json"), "w", encoding="utf-8") as f:
    json.dump(alld, f, ensure_ascii=False, indent=1)

with open(os.path.join("out", "discover_report.txt"), "w", encoding="utf-8") as f:
    f.write("queries=%d unique_domains=%d\n\n" % (len(QUERIES), len(domains)))
    for d, v in ranked:
        f.write("  %3d  %-34s %s\n" % (v["n"], d, classify(d)))

print("queries=%d unique_domains=%d" % (len(QUERIES), len(domains)))
print("ALL:")
for d, v in ranked:
    print("  %3d  %-34s %s  %s" % (v["n"], d, classify(d), v["sample"][:70]))
