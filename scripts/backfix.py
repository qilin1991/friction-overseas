#!/usr/bin/env python3
"""回填修复：解码 Google News 死链为真实媒体 URL + 补翻英文标题。

输入 data/pending_backfix.json: [{"id","title","url","resolve","translate"}]
输出 data/backfix_result.json:  [{"id","real_url","url_way","title_zh"}]

解码通道（Azure 出口下 Google 可达）：
  1. batchexecute（news.google.com 官方解码端点）
  2. Bing Web Search RSS 反查（标题去尾部 " - 域名" 后精确搜索，取首个非 bing 结果）
翻译通道：Google gtx 免费端点（sl=auto → tl=zh-CN）。
"""
import base64
import json
import os
import re
import sys
import time
import urllib.parse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
IN_PATH = "data/pending_backfix.json"
OUT_PATH = "data/backfix_result.json"


def _session():
    s = requests.Session()
    s.headers.update(UA)
    return s


S = _session()


# ---------- 1) Google News 解码 ----------
def gnews_decode(gnews_url: str) -> str | None:
    """batchexecute 官方端点解码，返回真实 URL 或 None。"""
    m = re.search(r"articles/([^?/]+)", gnews_url)
    if not m:
        return None
    art_id = m.group(1)
    try:
        r1 = S.get(f"https://news.google.com/rss/articles/{art_id}", timeout=20)
        if r1.status_code != 200:
            return None
        sid = re.search(r'data-n-a-sid="([^"]+)"', r1.text)
        sig = re.search(r'data-n-a-signature="([^"]+)"', r1.text)
        ts = re.search(r'data-n-a-timestamp="(\d+)"', r1.text)
        if not (sid and sig and ts):
            return None
        inner = json.dumps([art_id, sid.group(1), sig.group(1),
                            int(ts.group(1)), True])
        freq = json.dumps([[[ "Fbv4je", inner, None, "generic"]]],
                          separators=(",", ":"))
        r2 = S.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                    data={"f.req": freq}, timeout=20)
        if r2.status_code != 200:
            return None
        mm = re.search(r'\\"(https?://(?!news\.google)[^"\\]+)\\"', r2.text)
        return mm.group(1) if mm else None
    except Exception:
        return None


# ---------- 2) Bing Web Search 反查 ----------
_DOMAIN_TAIL = re.compile(r"\s+[-–—]\s+([A-Za-z0-9.-]+\.[a-z]{2,})\s*$")


def bing_web_lookup(title: str) -> str | None:
    """Bing 网页搜索（RSS 输出）反查真实文章 URL。"""
    t = title.strip()
    dom = None
    dm = _DOMAIN_TAIL.search(t)
    if dm:
        dom = dm.group(1).lower()
        t = _DOMAIN_TAIL.sub("", t)
    q = f'"{t[:70]}"'
    if dom:
        q += f" site:{dom}"
    try:
        r = S.get("https://www.bing.com/search",
                  params={"q": q, "format": "rss", "count": "5"}, timeout=20)
        if r.status_code != 200 or "<item" not in r.text:
            return None
        # 逐个 <item> 抽 <link>，取第一个非 bing/microsoft 的真实链接
        for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:5]:
            lm = re.search(r"<link>([^<]+)</link>", it)
            if not lm:
                continue
            link = lm.group(1).strip()
            if "bing.com" in link or "microsoft" in link or "go.micro" in link:
                continue
            if link.startswith("http"):
                return link
    except Exception:
        pass
    return None


# ---------- 3) Google gtx 翻译 ----------
def gtx_translate(text: str, retry: int = 2) -> str | None:
    text = (text or "").strip()
    if not text or len(text) > 450:
        return None
    for _ in range(retry + 1):
        try:
            r = S.get("https://translate.googleapis.com/translate_a/single",
                      params={"client": "gtx", "sl": "auto", "tl": "zh-CN",
                              "dt": "t", "q": text},
                      timeout=15)
            if r.status_code == 200:
                data = r.json()
                seg = "".join(p[0] for p in data[0] if p and p[0])
                seg = (seg or "").strip()
                if seg and seg != text:
                    return seg
                return None
        except Exception:
            time.sleep(1.5)
    return None


def main():
    only_fail = "--rerun-failed" in sys.argv
    items = json.load(open(IN_PATH, encoding="utf-8"))

    # 断点续跑：已有结果里成功的不再重复
    done = {}
    if os.path.exists(OUT_PATH) and only_fail:
        for r in json.load(open(OUT_PATH, encoding="utf-8")):
            if r.get("real_url") or r.get("title_zh"):
                done[r["id"]] = r
    results = list(done.values())

    n_res_ok = n_tr_ok = 0
    for i, it in enumerate(items):
        if it["id"] in done:
            continue
        rec = {"id": it["id"], "real_url": None, "url_way": None,
               "title_zh": None}
        if it.get("resolve"):
            url = gnews_decode(it["url"])
            way = "gdecode"
            if not url:
                time.sleep(0.4)
                url = bing_web_lookup(it["title"])
                way = "bing-web"
            if url:
                rec["real_url"] = url
                rec["url_way"] = way
                n_res_ok += 1
            time.sleep(0.6)
        if it.get("translate"):
            zh = gtx_translate(it["title"])
            if zh:
                rec["title_zh"] = zh
                n_tr_ok += 1
            time.sleep(0.3)
        results.append(rec)
        if (i + 1) % 25 == 0:
            print(f"[{i+1}/{len(items)}] 解码成功{n_res_ok} 翻译成功{n_tr_ok}",
                  flush=True)
            json.dump(results, open(OUT_PATH, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)

    json.dump(results, open(OUT_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"完成: 解码 {n_res_ok} 翻译 {n_tr_ok} / 共 {len(items)}")


if __name__ == "__main__":
    main()
