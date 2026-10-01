#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：逐个检查 collect_overseas.py 里的 SOURCES 配置是否还有效。

对每个源输出：
  · 入口 HTTP 状态 / 页面大小 / 是否被 WAF 拦
  · 页面里有多少条链接匹配 link_re（命中数）
  · 命中为 0 时，输出「站内链接路径样本」，反推正确的正则

输出：out/source_diag.json

为什么要这个脚本
----------------
「抓 0 条」有三种完全不同的原因，必须区分开：
  · HTTP 403 / WAF 页      → IP 被封，要靠代理或换出口
  · HTTP 200 links=N hit=0 → 入口可达但正则失效，改 link_re 即可
  · HTTP 200 links=0       → 页面是 SPA，链接靠 JS 渲染，得换列表页或 RSS
对症才能下药，所以把三者都测出来。
"""
import concurrent.futures as cf
import json
import os
import re
import sys
from urllib.parse import urljoin, urlsplit

import requests

# 直接复用采集器的源配置，保证诊断对象与线上一致
# （collect_overseas.py 在 scripts/ 下，需要把该目录也加进 sys.path）
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_overseas import SOURCES, UA  # noqa: E402

OUT_DIR = "out"
BLOCK_MARKERS = ["just a moment", "access denied", "cf-browser",
                 "checking your browser", "attention required",
                 "enable javascript", "captcha"]

# WAF 站的 RSS 候选：RSS 端点常常不走 Cloudflare 的浏览器挑战，
# 对「正文页被拦但 feed 可读」的 CMS 是最省事的突破口。
RSS_CANDIDATES = {
    "Tire Business": [
        "https://www.tirebusiness.com/rss.xml",
        "https://www.tirebusiness.com/feed",
        "https://www.tirebusiness.com/rss/all",
    ],
    "CCJ Digital": [
        "https://www.ccjdigital.com/rss.xml",
        "https://www.ccjdigital.com/feed",
    ],
    "TMD Friction": [
        "https://www.tmdfriction.com/en/rss/",
        "https://www.tmdfriction.com/rss.xml",
        "https://www.tmdfriction.com/en/news/rss",
    ],
    "SAE International": [
        "https://www.sae.org/rss/news.xml",
        "https://www.sae.org/news/rss",
        "https://www.sae.org/rss",
    ],
}


def probe_rss(url: str) -> dict:
    """探一个 RSS 候选端点：200 且含 <item>/<entry> 才算通。"""
    rec = {"url": url}
    try:
        r = requests.get(url, headers=UA, timeout=20)
        rec["status"] = r.status_code
        rec["ctype"] = r.headers.get("content-type", "")[:60]
        rec["len"] = len(r.text)
        low = r.text[:4000].lower()
        rec["blocked"] = any(m in low for m in BLOCK_MARKERS)
        n_item = low.count("<item") + low.count("<entry")
        rec["items"] = n_item
        rec["ok"] = (r.status_code == 200 and n_item > 0 and not rec["blocked"])
        if rec["ok"]:
            # 抽第一条的链接，确认是真 feed
            m = re.search(r"<link[^>]*>\s*(https?://[^<\s]+)", r.text)
            rec["first_link"] = m.group(1) if m else ""
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, e)
        rec["ok"] = False
    return rec


def path_of(url, base):
    """把绝对 URL 压成站内路径，便于观察规律。"""
    try:
        sp = urlsplit(url)
        p = sp.path or "/"
        if sp.query:
            p += "?" + sp.query[:40]
        return p
    except Exception:
        return url[:80]


def diag_one(src):
    name = src["name"]
    urls = [src.get("list_url"), src.get("list_url2")]
    urls = [u for u in urls if u]
    rec = {"name": name, "probes": []}

    for lu in urls:
        p = {"list_url": lu}
        try:
            r = requests.get(lu, headers=UA, timeout=25)
            p["status"] = r.status_code
            p["len"] = len(r.text)
            low = r.text[:20000].lower()
            p["blocked"] = any(m in low for m in BLOCK_MARKERS)
            p["final"] = r.url

            if r.status_code != 200:
                rec["probes"].append(p)
                continue

            # 统计页面里所有站内链接
            base_host = urlsplit(src["base"]).netloc
            all_links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
            internal = []
            for h in all_links:
                if h.startswith("#") or h.startswith("mailto:") \
                        or h.startswith("javascript:"):
                    continue
                full = urljoin(lu, h)
                if urlsplit(full).netloc == base_host:
                    internal.append(full)
            p["internal_links"] = len(internal)

            # 用采集器的正则去匹配（含 exclude_url，与线上行为一致）
            try:
                rx = re.compile(src["link_re"])
                _exc = src.get("exclude_url")
                erx = re.compile(_exc) if _exc else None
            except Exception as e:
                p["regex_error"] = str(e)
                rec["probes"].append(p)
                continue
            raw_hit = [u for u in internal if rx.search(u)]
            hit = [u for u in raw_hit if not (erx and erx.search(u))]
            p["hit"] = len(hit)
            p["hit_raw"] = len(raw_hit)
            p["hit_samples"] = [path_of(u, src["base"]) for u in hit[:5]]

            # 命中为 0：给出路径样本，供反推正则
            if not hit:
                seen, samples = set(), []
                for u in internal:
                    pp = path_of(u, src["base"])
                    # 取一级路径做粗分类
                    seg = pp.split("/")[1] if pp.count("/") >= 1 else ""
                    key = seg
                    if key in seen:
                        continue
                    seen.add(key)
                    samples.append(pp[:90])
                    if len(samples) >= 18:
                        break
                p["path_samples"] = samples
        except Exception as e:
            p["error"] = "%s: %s" % (type(e).__name__, e)
        rec["probes"].append(p)

    # 汇总判定
    verdicts = []
    for p in rec["probes"]:
        if p.get("error"):
            verdicts.append("ERROR")
        elif p.get("blocked"):
            verdicts.append("WAF拦截")
        elif p.get("status") != 200:
            verdicts.append("HTTP%s" % p.get("status"))
        elif p.get("hit", 0) > 0:
            verdicts.append("OK(%d)" % p["hit"])
        elif p.get("internal_links", 0) == 0:
            verdicts.append("SPA无链接")
        else:
            verdicts.append("正则0命中(raw=%s)" % p.get("hit_raw", "?"))
    rec["verdict"] = " / ".join(verdicts)

    # WAF 站：额外探 RSS 出路
    if "WAF拦截" in rec["verdict"] or any(
            p.get("blocked") for p in rec["probes"]):
        cands = RSS_CANDIDATES.get(name, [])
        if cands:
            rec["rss"] = [probe_rss(u) for u in cands]
            if any(x.get("ok") for x in rec["rss"]):
                rec["verdict"] += " + RSS可用"
    return rec


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("诊断 %d 个源..." % len(SOURCES))
    results = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(diag_one, s): s for s in SOURCES}
        for fu in cf.as_completed(futs):
            src = futs[fu]
            try:
                rec = fu.result()
            except Exception as e:
                rec = {"name": src["name"], "verdict": "EXC:%s" % e,
                       "probes": []}
            results.append(rec)
            print("  %-26s %s" % (rec["name"], rec["verdict"]))

    results.sort(key=lambda r: r["name"])
    with open(os.path.join(OUT_DIR, "source_diag.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 打印需要处理的源详情
    print("\n" + "=" * 70)
    print("需要处理的源：")
    for r in results:
        v = r["verdict"]
        if "OK(" in v:
            continue
        print("-" * 70)
        print("【%s】%s" % (r["name"], v))
        for p in r["probes"]:
            if p.get("error"):
                print("   %s\n     ERROR %s" % (p["list_url"], p["error"]))
                continue
            print("   %s\n     HTTP%s len=%s links=%s hit=%s%s"
                  % (p["list_url"], p.get("status"), p.get("len"),
                     p.get("internal_links"), p.get("hit"),
                     " [WAF]" if p.get("blocked") else ""))
            for s in (p.get("path_samples") or [])[:12]:
                print("       ·", s)
        for x in (r.get("rss") or []):
            flag = "OK %d items" % x["items"] if x.get("ok") else \
                   (x.get("error") or "HTTP%s items=%s%s"
                    % (x.get("status"), x.get("items"),
                       " [WAF]" if x.get("blocked") else ""))
            print("   RSS %s -> %s" % (x["url"], flag))
            if x.get("first_link"):
                print("       首条: %s" % x["first_link"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        os.makedirs(OUT_DIR, exist_ok=True)
        tb = traceback.format_exc()
        with open(os.path.join(OUT_DIR, "source_diag_error.txt"), "w",
                  encoding="utf-8") as f:
            f.write(tb)
        print(tb)
        sys.exit(1)
