#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多搜索引擎探测：把 news.google.com 死链还原成真实原文直链。

逐条读 gnews_pending.txt（id|url|title），对每个标题尝试多个搜索引擎，
按标题相似度挑最匹配、且非搜索引擎/聚合站域名的真实直链。
统计每个引擎从 GitHub Actions(Azure) 出口的可达性与命中率，落盘 out/engine_report.txt。
"""
import json, os, re, base64, requests, time
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TOUT = 25
os.makedirs("out", exist_ok=True)

# ── 待处理 ──────────────────────────────────────────────
pending = []
with open("gnews_pending.txt", encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("|", 2)
        pid = parts[0]
        url = parts[1] if len(parts) > 1 else ""
        title = parts[2] if len(parts) > 2 else ""
        pending.append((pid, url, title))

# ── 引擎定义 ────────────────────────────────────────────
ENGINES = {
    "ddg_html":  "https://html.duckduckgo.com/html/?q={q}",
    "ddg_lite":  "https://lite.duckduckgo.com/lite/?q={q}",
    "yahoo":     "https://search.yahoo.com/search?p={q}",
    "yandex":    "https://yandex.com/search/?text={q}",
    "startpage": "https://www.startpage.com/sp/search?query={q}",
    "mojeek":    "https://www.mojeek.com/search?q={q}",
    "bing_web":  "https://www.bing.com/search?q={q}&setmkt=en-US&cc=US",
    "aol":       "https://search.aol.com/aol/search?q={q}",
    "ask":       "https://www.ask.com/web?q={q}",
    "searx_be":  "https://searx.be/search?q={q}&format=json",
    "searx_tie": "https://search.rhscz.eu/search?q={q}&format=json",
}

BLOCK = ("news.google.com", "bing.com", "duckduckgo.com", "yahoo.com",
         "yandex.com", "startpage.com", "mojeek.com", "aol.com", "ask.com",
         "searx", "microsoft.com", "msn.com", "google.com", "googleusercontent",
         "yimg.com", "search.", "consent.")

def norm(u):
    if not u:
        return None
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith("http"):
        return None
    low = u.lower()
    if any(b in low for b in BLOCK):
        return None
    return u

def extract(engine, html, resp):
    links = []
    try:
        if engine == "ddg_html":
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a.result__a"):
                h = a.get("href", "")
                if h.startswith("http"):
                    links.append(h)
                elif "uddg=" in h:
                    m = re.search(r"uddg=([^&]+)", h)
                    if m:
                        links.append(requests.utils.unquote(m.group(1)))
            for a in soup.select("a[href*='duckduckgo.com/l/?uddg=']"):
                m = re.search(r"uddg=([^&]+)", a.get("href", ""))
                if m:
                    links.append(requests.utils.unquote(m.group(1)))
        elif engine == "ddg_lite":
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a.result-link"):
                if a.get("href", "").startswith("http"):
                    links.append(a["href"])
        elif engine == "bing_web":
            soup = BeautifulSoup(html, "html.parser")
            for li in soup.select("li.b_algo"):
                a = li.select_one("h2 a") or li.select_one("a")
                if a and a.get("href"):
                    links.append(a["href"])
            for a in soup.select("a[href*='bing.com/ck/a']"):
                m = re.search(r"u=a1([^&]+)", a.get("href", ""))
                if m:
                    try:
                        links.append(base64.urlsafe_b64decode(m.group(1) + "==").decode("utf-8", "ignore"))
                    except Exception:
                        pass
        elif engine in ("yahoo", "yandex", "startpage", "mojeek", "aol", "ask"):
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href^='http']"):
                h = a.get("href", "")
                if norm(h):
                    links.append(h)
        elif engine.startswith("searx"):
            try:
                links = [i.get("url") for i in resp.json().get("results", []) if i.get("url")]
            except Exception:
                links = []
    except Exception:
        pass
    return [norm(x) for x in links if norm(x)]

def sim(a, b):
    sa = set(re.findall(r"[a-z0-9]+", a.lower()))
    sb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sb)

# ── 主循环 ──────────────────────────────────────────────
results = []
engine_hits = {e: 0 for e in ENGINES}
engine_reach = {e: 0 for e in ENGINES}
samples = []
for pid, url, title in pending:
    best = None
    bestscore = 0.0
    notes = {}
    for eng in ENGINES:
        try:
            r = requests.get(ENGINES[eng].format(q=requests.utils.quote(title)),
                             headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                             timeout=TOUT, allow_redirects=True)
            st = r.status_code
        except Exception as e:
            notes[eng] = "ERR:%s" % type(e).__name__
            continue
        engine_reach[eng] += 1
        if st != 200:
            notes[eng] = "HTTP%d" % st
            continue
        links = extract(eng, r.text, r)
        notes[eng] = "links=%d" % len(links)
        if links:
            scored = sorted(((sim(title, l), l) for l in links), reverse=True)
            s, l = scored[0]
            if s > 0.3:
                engine_hits[eng] += 1
            if s > bestscore:
                bestscore = s
                best = l
        if len(samples) < 40 and links:
            samples.append({"id": pid, "engine": eng, "top": links[:3]})
    ok = best is not None and bestscore >= 0.3
    results.append({"id": pid, "orig": url, "final": best or "", "ok": ok,
                    "score": round(bestscore, 3), "note": json.dumps(notes, ensure_ascii=False)})

# ── 输出 ────────────────────────────────────────────────
with open("gnews_resolved.jsonl", "w", encoding="utf-8") as f:
    for x in results:
        f.write(json.dumps(x, ensure_ascii=False) + "\n")

with open(os.path.join("out", "engine_report.txt"), "w", encoding="utf-8") as f:
    f.write("engine reach / hits (sim>=0.3):\n")
    for e in ENGINES:
        f.write("  %-10s reach=%d hits=%d\n" % (e, engine_reach[e], engine_hits[e]))
    f.write("\nlink samples:\n")
    for s in samples:
        f.write("  id=%s eng=%s %s\n" % (s["id"], s["engine"], " | ".join(s["top"][:2])))

okc = sum(1 for x in results if x["ok"])
print("DONE total=%d ok=%d fail=%d" % (len(results), okc, len(results) - okc))
print("engine_hits:", json.dumps(engine_hits, ensure_ascii=False))
for x in results:
    print("  id=%s ok=%s score=%s final=%s" % (x["id"], x["ok"], x["score"], x["final"][:90]))
