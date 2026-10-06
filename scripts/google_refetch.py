#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google News 跳转壳解析器 —— 跑在有海外网络的机器上（GitHub Actions runner）

问题
----
Friction Hub 的采集链路里，Google News RSS 给出的 link 形如
    https://news.google.com/rss/articles/CBMi<AES 加密串>?oc=5
它是**跳转壳**，不是文章真地址。VPS（国内）访问 news.google.com 恒返回 000，
导致这 277 篇永远停在「只有标题」状态 —— 不是文章没价值，是我们从没找到它在哪。

三级解析
--------
  L1  直接 GET 跟随重定向。旧格式（2024 前）会 302 到源站，成本最低。
  L2  POST _/DotsSplashUi/data/batchexecute。新版落户/newimmunosuppression 页面把
      真 URL 藏在 c-wiz 标签的 data-n-a-sg / data-n-a-ts 里，需要用签名换。
  L3  Playwright 真实浏览器加载，等 URL 稳定。最慢但最鲁棒，作为兜底。

解析出真 URL 后顺手抓正文（复用一套通用正文提取），一并写进输出 JSON，
VPS 侧就不用再出海一次。

用法
----
    python3 google_refetch.py --pending data/pending_google.json \
                              --out data/google_refetch.json \
                              [--limit 50] [--mode auto|http|hooks|browser]

输出每条：
    {"article_id": 123, "gurl": "…CBMi…", "real_url": "https://…",
     "text": "正文…", "title_zh": null, "ok": true, "stage": "L2", "err": null}
"""
import argparse
import base64
import json
import re
import sys
import time
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
GOOGLE_HOSTS = ("news.google.com",)

# 正文容器优先级
SELECTORS = ["article", "[role=main] article", "main",
             ".article-content", ".article_content", ".article-body",
             ".articleBody", ".post-content", ".entry-content",
             ".story-body", ".storyBody", ".c-entry-content",
             ".content-detail", ".detail-content", ".rich-text",
             "#article-body", "#content", ".content"]


# ────────────────────────────── L1 / L2 ──────────────────────────────
def _l1_http(gurl, timeout=20):
    """旧式跳转壳：直接跟随重定向即可拿到源站地址。"""
    try:
        r = requests.get(gurl, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except Exception as e:
        return None, f"net:{type(e).__name__}"
    host = urlparse(r.url).netloc
    if host and not any(h in host for h in GOOGLE_HOSTS):
        return r.url, r.status_code
    return None, r.status_code


def _extract_sig(html):
    """从 Google 中转页里挖出 (signature, timestamp)。"""
    sig = re.search(r'data-n-a-sg="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    if sig and ts:
        return sig.group(1), ts.group(1)
    sig = re.search(r'"sg":"([^"]+)"', html)
    ts = re.search(r'"ts":(\d+)', html)
    if sig and ts:
        return sig.group(1), ts.group(1)
    return None, None


def _l2_hooks(gurl, timeout=25):
    """新版跳转壳：用页面签名去 batchexecute 换真实 URL。"""
    try:
        r = requests.get(gurl, headers=HEADERS, timeout=timeout)
        html = r.text
    except Exception as e:
        return None, f"net:{type(e).__name__}"

    aid = re.search(r'/rss/articles/([A-Za-z0-9_\-]+)', gurl)
    if not aid:
        return None, "bad-gurl"
    art_id = aid.group(1)
    sig, ts = _extract_sig(html)
    if not sig:
        return None, "no-signature"

    payload = [[["Fbv4je",
                 json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1,
                                            "US:en", None, 1, None, None, None, None,
                                            None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1,
                                            None, 0, 0, None, 0],
                                           art_id, int(ts) if str(ts).isdigit() else 0,
                                           sig]),
                 None, "generic"]]]
    try:
        rp = requests.post(BATCH_URL, headers=HEADERS, timeout=timeout,
                           data={"f.req": json.dumps(payload)})
    except Exception as e:
        return None, f"batch:{type(e).__name__}"
    if rp.status_code != 200:
        return None, f"batch{rp.status_code}"

    for line in rp.text.split("\n"):
        if "garturlreq" not in line and "http" not in line:
            continue
        try:
            obj = json.loads(line)
            for item in obj:
                if not isinstance(item, list) or len(item) < 3:
                    continue
                inner = item[2]
                if isinstance(inner, str) and inner.startswith("http"):
                    return inner, 200
                if isinstance(inner, str):
                    try:
                        d = json.loads(inner)
                        if isinstance(d, list) and d and isinstance(d[0], str) \
                                and d[0].startswith("http"):
                            return d[0], 200
                    except Exception:
                        pass
        except Exception:
            continue
    # 兜底：正文 CMB 体里 often 直接可见 http(s)
    m = re.findall(r'https?://[^\\"\s<>]{20,300}', rp.text)
    m = [x for x in m if not any(h in x for h in GOOGLE_HOSTS)
         and not x.endswith((".js", ".css", ".png", ".jpg", ".svg"))]
    if m:
        return m[0], 200
    return None, "no-url-in-batch"


# ────────────────────────────── L3 浏览器 ──────────────────────────────
def _l3_browser(gurl, timeout=45):
    """兜底：真浏览器加载，等地址栏停稳。需要 playwright 已安装 chromium。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "no-playwright"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page(user_agent=UA)
            try:
                pg.goto(gurl, timeout=timeout * 1000, wait_until="domcontentloaded")
                last = pg.url
                for _ in range(12):          # 最多等 6 秒让 JS 完成跳转
                    time.sleep(0.5)
                    if pg.url != last:
                        last = pg.url
                        if not any(h in urlparse(last).netloc for h in GOOGLE_HOSTS):
                            return last, 200
                    else:
                        if not any(h in urlparse(last).netloc for h in GOOGLE_HOSTS):
                            return last, 200
            finally:
                b.close()
    except Exception as e:
        return None, f"pw:{type(e).__name__}"
    return None, "browser-no-redirect"


def resolve(gurl, mode="auto", browser_on_fail=True):
    """三级解析，返回 (real_url, stage, err)"""
    order = {"http": ["L1"], "hooks": ["L2"], "browser": ["L3"],
             "auto": ["L1", "L2", "L3"]}[mode]
    if not browser_on_fail:
        order = [x for x in order if x != "L3"]

    err = None
    for stage in order:
        if stage == "L1":
            u, e = _l1_http(gurl)
        elif stage == "L2":
            u, e = _l2_hooks(gurl)
        else:
            u, e = _l3_browser(gurl)
        if u:
            return u, stage, None
        err = f"{stage}:{e}"
    return None, None, err or "unknown"


# ────────────────────────────── 正文 ──────────────────────────────
def fetch_text(url, timeout=30, min_chars=180):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        return "", f"net:{type(e).__name__}"
    if r.status_code >= 400:
        return "", f"http{r.status_code}"
    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        return "", f"soup:{type(e).__name__}"
    for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        bad.decompose()

    node = None
    for sel in SELECTORS:
        try:
            n = soup.select_one(sel)
        except Exception:
            continue
        if n and len(n.get_text(" ", strip=True)) >= min_chars:
            node = n
            break
    if node is None:
        ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(x for x in ps if len(x) > 8)
    else:
        text = node.get_text(" ", strip=True)
    text = unescape(re.sub(r"\s{2,}", " ", text)).strip()
    return text, (None if text else "empty")


# ────────────────────────────── main ──────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", required=True, help="VPS 导出的待解析清单 JSON")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mode", default="auto", choices=["auto", "http", "hooks", "browser"])
    ap.add_argument("--no-browser", action="store_true", help="禁用 L3 浏览器兜底")
    ap.add_argument("--skip-text", action="store_true", help="只解 URL 不抓正文")
    a = ap.parse_args()

    pend = json.load(open(a.pending, encoding="utf-8"))
    if a.limit:
        pend = pend[:a.limit]
    print(f"[google_refetch] 待处理 {len(pend)} 条 mode={a.mode}", flush=True)

    out, n_ok, stages = [], 0, {}
    for i, row in enumerate(pend, 1):
        aid, gurl = row.get("article_id"), row.get("url")
        real, stage, err = resolve(gurl, a.mode, not a.no_browser)
        stages[stage or "FAIL"] = stages.get(stage or "FAIL", 0) + 1
        text, terr = ("", None)
        if real and not a.skip_text:
            text, terr = fetch_text(real)
        ok = bool(real and (text or a.skip_text))
        n_ok += 1 if ok else 0
        out.append({"article_id": aid, "gurl": gurl, "real_url": real,
                    "text": text, "ok": ok, "stage": stage,
                    "err": err or terr})
        if i % 10 == 0 or i == len(pend):
            print(f"  ...{i}/{len(pend)} 成功={n_ok} 阶段={stages}", flush=True)
        time.sleep(0.4)

    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cnt = sum(1 for x in out if x["ok"])
    print(f"[google_refetch] 完成：{cnt}/{len(pend)} 成功，"
          f"正文 {sum(len(x['text']) for x in out)} 字 → {a.out}")


if __name__ == "__main__":
    sys.exit(main())
