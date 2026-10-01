#!/usr/bin/env python3
"""探针二：测代理阅读器能否绕过 WAF。

针对直连被 Cloudflare / Akamai 拦死的站点，试第三方"阅读器 /
文本抽取"服务能否拿到内容。这些服务跑在境外、有自己的出口 IP 池，
可能不在目标站的封锁名单里。

被测通道：
  1. r.jina.ai      —— 免费阅读器，返回 markdown
  2. allorigins.win —— 公共 CORS 代理，返回原始 HTML
  3. textance       —— 文本抽取
  4. corsproxy.io   —— 另一公共代理

输出：out/proxy_probe.json
"""
import json
import os
import sys
import time
from urllib.parse import quote

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
}

# 目标站（直连被 WAF 拦的）
TARGETS = [
    ("Tire Business",   "https://www.tirebusiness.com/"),
    ("CCJ Digital",     "https://www.ccjdigital.com/"),
    ("TMD Friction",    "https://www.tmdfriction.com/en/news/"),
    ("Automotive News", "https://www.autonews.com/"),
    ("Counterman",      "https://www.counterman.com/"),
]

# 拦截特征：命中说明拿到的还是 WAF 页面，不是真内容
BLOCK_MARKERS = [
    "just a moment", "enable javascript and cookies",
    "access denied", "attention required", "cf-browser-verification",
    "checking your browser", "ddos protection",
]


def looks_blocked(text: str) -> bool:
    low = text[:4000].lower()
    return any(m in low for m in BLOCK_MARKERS)


def via_jina(url: str, timeout: int = 30):
    """r.jina.ai —— 免费阅读器，返回 markdown 文本。"""
    try:
        r = requests.get(f"https://r.jina.ai/{url}",
                         headers={**UA, "Accept": "text/plain"}, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        txt = r.text
        if looks_blocked(txt):
            return None, "返回的仍是 WAF 页"
        return txt, None
    except Exception as e:
        return None, f"{type(e).__name__}: {repr(e)[:60]}"


def via_allorigins(url: str, timeout: int = 30):
    """allorigins —— 公共代理，返回原始 HTML。"""
    try:
        api = f"https://api.allorigins.win/raw?url={quote(url, safe='')}"
        r = requests.get(api, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        txt = r.text
        if looks_blocked(txt):
            return None, "返回的仍是 WAF 页"
        return txt, None
    except Exception as e:
        return None, f"{type(e).__name__}: {repr(e)[:60]}"


CHANNELS = [
    ("r.jina.ai", via_jina),
    ("allorigins", via_allorigins),
]


def main():
    os.makedirs("out", exist_ok=True)
    out = {"results": [], "summary": {}}

    for name, url in TARGETS:
        print(f"\n>>> {name}  {url}")
        rec = {"name": name, "url": url, "channels": {}}
        for ch_name, fn in CHANNELS:
            txt, err = fn(url)
            ok = txt is not None and len(txt) > 800
            rec["channels"][ch_name] = {
                "ok": ok,
                "bytes": len(txt) if txt else 0,
                "error": err,
                "sample": (txt[:160].replace("\n", " ") if txt else ""),
            }
            mark = "✅" if ok else "⛔"
            size = len(txt) if txt else 0
            print(f"    {mark} {ch_name:<14} {size:>7}B  {err or ''}")
            time.sleep(1.5)
        out["results"].append(rec)

    # 汇总：每个通道通了几站
    for ch_name, _ in CHANNELS:
        n = sum(1 for r in out["results"] if r["channels"][ch_name]["ok"])
        out["summary"][ch_name] = f"{n}/{len(TARGETS)}"
        print(f"\n{ch_name}: 突破 {n}/{len(TARGETS)}")

    with open("out/proxy_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n→ out/proxy_probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
