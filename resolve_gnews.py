#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发现信源（第二轮增强）：用 Bing（Azure 出口）搜索「摩擦材料 / 制动器」主题，
第一轮泛关键词 + 第二轮制造商/技术词/论文源/法规市场，收集返回域名，过滤门户/社交/
通用站，筛出行业信源并按类别预标注。
输出：out/discover_report.txt(带频次+分类)、out/discover_domains.json(全量)、
      out/discover_sources.jsonl(样本明细)
"""
import json, os, re, base64, requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TOUT = 25
os.makedirs("out", exist_ok=True)

# ---------- 关键词（两轮合并）----------
QUERIES = [
    # 第一轮：泛主题
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
    # 第二轮 A：制造商官网
    "Brembo brake pad friction material",
    "Bosch brake pad",
    "Ferodo brake pad",
    "Textar brake pad",
    "Carlisle brake friction",
    "TMD Friction brake pad",
    "Bendix brake pad",
    "Akebono brake pad",
    "Nisshinbo brake friction",
    "Fras-le brake pad",
    "Federal-Mogul brake pad",
    "MAT Holdings brake pad",
    "Sangsin brake pad",
    "Continental brake pad",
    "ATE brake pad",
    "Pagid brake pad",
    "Wagner brake pad",
    "Raybestos brake pad",
    "EBC brake pad",
    "Hawk Performance brake pad",
    "Wilwood brake pad",
    "Centric brake pad",
    "Power Stop brake pad",
    "SBS braking brake pad",
    "Galfer brake pad",
    "Napa brake pad",
    "O'Reilly brake pad",
    # 第二轮 B：技术深词
    "brake squeal noise NVH friction",
    "brake judder hot judder",
    "brake fade thermal stability friction",
    "copper-free brake pad regulation California",
    "brake pad eco-label environmental",
    "low metallic brake pad formulation",
    "semi-metallic brake pad formulation",
    "ceramic brake pad versus organic",
    "friction coefficient wear brake dynamometer",
    "asbestos free friction material formulation",
    "green strength friction material",
    "brake pad FMSI code",
    "ECE R90 brake pad regulation",
    "brake friction material patent",
    # 第二轮 C：论文源 / 标准 / 会议
    "SAE International brake friction paper",
    "Tribology International friction material",
    "Wear journal brake friction",
    "friction material conference brake",
    "STLE friction lubrication engineers",
    # 第二轮 D：法规 / 市场
    "brake friction material market report aftermarket",
    "brake pad market size forecast",
    "California copper brake pad regulation",
]

BING_WEB = "https://www.bing.com/search?q={q}&setmkt=en-US&cc=US&count=50"
BING_NEWS = "https://www.bing.com/news/search?q={q}&setmkt=en-US"

BLOCK = ("bing.com", "google.com", "googleusercontent", "yahoo.com", "yandex.com",
         "duckduckgo.com", "startpage.com", "mojeek.com", "aol.com", "ask.com", "searx",
         "wikipedia.org", "youtube.com", "twitter.com", "facebook.com", "linkedin.com",
         "microsoft.com", "apple.com", "amazon.com", "reddit.com", "pinterest.com",
         "instagram.com", "tiktok.com", "msn.com", "bingj", "goo.gl", "bit.ly",
         "googleapis", "gstatic", "cloudfront", "w3.org", "aka.ms", "wikihow.com",
         "quora.com", "medium.com", "github.com", "stackoverflow.com", "pinterest",
         "craigslist", "ebay.com")

STATIC = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".json",
          ".woff", ".woff2", ".ttf", ".xml", ".pdf")

# 域名 -> 类别预标注
MANUF = ("brembo", "bosch", "ferodo", "textar", "carlisle", "tmd-friction", "tmd",
         "bendix", "akebono", "nisshinbo", "fras-le", "frasle", "federal-mogul",
         "fedmogul", "matholdings", "mat-holdings", "sangsin", "continental",
         "ate", "pagid", "wagner", "raybestos", "ebcbrakes", "ebc", "hawkperformance",
         "hawk", "wilwood", "centric", "powerstop", "sbs", "galfer", "napaonline",
         "napa", "oreillyauto", "oreilly", "brakepartsinc", "icer", "jurid", "mintex",
         "hitachi", "fbk", "don-brake", "sponsor", "mak", "brembo", "fdb", "tarox",
         "ecs", "rockauto", "advanceautoparts", "autozone", "partsgeek", "carparts")
ACADEMIC = ("sciencedirect", "springer", "mdpi", "frontiersin", "tribology", "stle",
            "sae", "researchgate", "sciencedaily", "eurekalert", "nature", "wiley",
            "tandfonline", "asm", "ceramics", "azom", "nasa", "ieee", "osti",
            "researchsquare", "arxiv")
GOVREG = ("epa.gov", "transport", "unece", "europa.eu", "gov", "nhtsa", "ec.europa",
          "defra", "europarl")
MEDIA = ("truckinginfo", "fleetowner", "ccjdigital", "trucknews", "automotiveworld",
         "just-auto", "hotrod", "autoevolution", "motortrend", "autoguide", "thedrive",
         "autoblog", "carthrottle", "evo.co", "zigwheels", "progressiverailroading",
         "railway-technology", "prnewswire", "businesswire", "wardsauto", "aftermarket",
         "freightwaves", "fleetequipment", "fleetmaintenance", "truckersnews", "landline")
MARKET = ("marketresearch", "grandviewresearch", "fortunebusinessinsights",
          "researchandmarkets", "marketsandmarkets", "persistencemarketresearch",
          "cognitivemarketresearch", "dataintelo", "verifiedmarketreports",
          "thebrainyinsights", "maximizemarketresearch", "straitsresearch")

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
        if "bing.com" in h.lower():
            continue
        if h.lower().endswith(STATIC):
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

# 全量域名 json
alld = {}
for d, v in domains.items():
    alld[d] = {"n": v["n"], "sample": v["sample"], "cls": classify(d)}
with open(os.path.join("out", "discover_domains.json"), "w", encoding="utf-8") as f:
    json.dump(alld, f, ensure_ascii=False, indent=1)

with open(os.path.join("out", "discover_report.txt"), "w", encoding="utf-8") as f:
    f.write("queries=%d unique_domains=%d\n\n" % (len(QUERIES), len(domains)))
    for d, v in ranked:
        f.write("  %3d  %-32s %s\n" % (v["n"], d, classify(d)))

print("queries=%d unique_domains=%d" % (len(QUERIES), len(domains)))
print("TOP40:")
for d, v in ranked[:40]:
    print("  %3d  %-32s %s" % (v["n"], d, classify(d)))
