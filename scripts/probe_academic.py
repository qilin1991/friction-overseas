# -*- coding: utf-8 -*-
"""探测学术源在 GitHub Actions（Azure 出口）的可达性。

背景：Google Scholar / 百度学术 / ScienceDirect 从本机（家宽）和 VPS（腾讯云）都拿不到：
  - scholar.google.com      curl 000（DNS 污染）
  - xueshu.baidu.com        search 302 → wappass 图形验证码；详情页 403
  - sciencedirect.com       403（反爬）
三者在 Azure 出口是否可用，必须先实测，别猜。

输出 out/academic_probe.json：
  [{name, url, final_url, code, size, title, verdict, signal, snippet}]
"""
import json
import os
import re
import sys
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HDR = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

TARGETS = [
    # --- Google Scholar ---
    ("google_scholar",      "https://scholar.google.com/scholar?hl=en&q=brake+friction+material"),
    ("google_scholar_cn",   "https://scholar.google.com/scholar?hl=zh-CN&q=" + urllib.parse.quote("刹车片 摩擦材料")),
    # --- 百度学术 ---
    ("baidu_xueshu",        "https://xueshu.baidu.com/s?wd=brake+friction"),
    ("baidu_xueshu_cn",     "https://xueshu.baidu.com/s?wd=" + urllib.parse.quote("刹车片 摩擦材料")),
    # --- ScienceDirect ---
    ("sciencedirect_search", "https://www.sciencedirect.com/search?qs=brake%20friction"),
    ("sciencedirect_api",   "https://www.sciencedirect.com/search/api/search?qs=brake%20friction&show=25"),
    ("sciencedirect_journal", "https://www.sciencedirect.com/journal/wear"),
    # --- 对照组（已知可用/不可用） ---
    ("crossref",            "https://api.crossref.org/works?query.bibliographic=brake&rows=1"),
    ("europepmc",           "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=brake&format=json&pageSize=1"),
    ("semanticscholar",     "https://api.semanticscholar.org/graph/v1/paper/search?query=brake%20friction&limit=3&fields=title,year,citationCount,venue"),
    # --- 中文库 ---
    ("cnki_home",           "https://kns.cnki.net/kns8s/defaultresult/index"),
    ("wanfang",             "https://s.wanfangdata.com.cn/paper?q=" + urllib.parse.quote("刹车片")),
    ("cqvip",               "https://qikan.cqvip.com/Qikan/Search/Index"),
]

SIGNALS = {
    "google_scholar":     [(r'class="gs_ri"', "结果条目 gs_ri"), (r"gs_r gs_or gs_scl", "结果条目 gs_r"),
                           (r"/sorry/", "被判定为机器人 /sorry/"), (r"unusual traffic", "异常流量提示")],
    "baidu_xueshu":       [(r"sc_default_result", "结果条目"), (r"wappass|tuxing_v2|captcha", "图形验证码"),
                           (r"sc_content", "结果内容块")],
    "sciencedirect":      [(r"js-react|react-app|__INITIAL_STATE__", "React SPA"),
                           (r'"searchResults"|"results":', "JSON 结果"), (r"Just a moment|cf-challenge", "CF 挑战")],
    "cnki":               [(r"kns8s|result-table", "KNKI 结果"), (r"验证|verify", "验证")],
    "wanfang":            [(r"normal-list|result", "结果列表")],
    "cqvip":              [(r"search-result|qikan-list", "结果列表")],
}


def verdict_for(name, code, text):
    t = text or ""
    low = t.lower()
    if code == 0:
        return "UNREACHABLE", ""
    if code in (401, 403):
        return "BLOCKED", ""
    if code == 429:
        return "RATE_LIMITED", ""
    if code != 200:
        return f"HTTP_{code}", ""
    # 200 后按内容判
    if "Just a moment" in t or "cf-challenge" in low or "Checking your browser" in t:
        return "CF_CHALLENGE", ""
    if "wappass" in t or "tuxing_v2" in t or "请输入验证码" in t:
        return "CAPTCHA", ""
    if "/sorry/" in t or "unusual traffic" in low:
        return "BOT_WALL", ""
    for key in SIGNALS:
        if key in name:
            hits = [lbl for rx, lbl in SIGNALS[key] if re.search(rx, t, re.I)]
            if "结果条目" in " ".join(hits) or "结果内容块" in " ".join(hits) or "结果" in " ".join(hits):
                return "OK_RESULTS", "; ".join(hits)
            if hits:
                return "OK_NO_RESULTS", "; ".join(hits)
            return "OK_UNKNOWN", ""
    return "OK", ""


def run_one(name, url, sess):
    out = {"name": name, "url": url}
    t0 = time.time()
    try:
        r = sess.get(url, headers=HDR, timeout=25, allow_redirects=True)
        text = r.text or ""
        out.update({
            "code": r.status_code,
            "final_url": r.url,
            "size": len(text),
            "title": (re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I).group(1).strip()[:120]
                      if re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I) else ""),
            "snippet": re.sub(r"\s+", " ", text[:400]),
        })
        v, sig = verdict_for(name, r.status_code, text)
        out["verdict"] = v
        out["signal"] = sig
    except requests.exceptions.SSLError as e:
        out.update({"code": 0, "verdict": "SSL_ERROR", "snippet": str(e)[:200], "size": 0})
    except requests.exceptions.Timeout:
        out.update({"code": 0, "verdict": "TIMEOUT", "snippet": "", "size": 0})
    except Exception as e:
        out.update({"code": 0, "verdict": "CONN_FAIL", "snippet": f"{type(e).__name__}: {e}"[:200],
                    "size": 0})
    out["elapsed"] = round(time.time() - t0, 2)
    return out


def main():
    os.makedirs("out", exist_ok=True)
    sess = requests.Session()
    rows = []
    for name, url in TARGETS:
        row = run_one(name, url, sess)
        rows.append(row)
        print("[%-22s] %-14s code=%s size=%s (%ss) %s" % (
            row["name"], row.get("verdict", "?"), row.get("code", "-"),
            row.get("size", "-"), row.get("elapsed"), row.get("signal", "")), flush=True)
        time.sleep(1.5)

    json.dump({"exit_ip": _exit_ip(sess), "rows": rows},
              open("out/academic_probe.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n=== 汇总 ===")
    for r in rows:
        print("  %-22s %s" % (r["name"], r.get("verdict")))
    print("ACADEMIC_PROBE_DONE")


def _exit_ip(sess):
    for u in ("https://api.ipify.org?format=json", "https://ifconfig.me/ip"):
        try:
            r = sess.get(u, timeout=15)
            return r.json().get("ip") if "ipify" in u else r.text.strip()
        except Exception:
            continue
    return "unknown"


if __name__ == "__main__":
    main()
