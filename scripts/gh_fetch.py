#!/usr/bin/env python3
"""通用单页抓取（GitHub Actions 侧）。

为什么要它：VPS（腾讯云）出口被 Cloudflare WAF / 地理封锁挡住的站点，
换成 Actions（Azure 海外机房）的出口常能直接拿到。本脚本由
`.github/workflows/fetch-url.yml` 通过 workflow_dispatch 调用，
结果写成 page.json 由 artifact 回传，VPS 侧 `/wproxy` 的 gh 出口取用。

用法:
  python3 scripts/gh_fetch.py --url https://example.com --mode html --out page.json
  python3 scripts/gh_fetch.py --url https://example.com --mode text --browser
"""

import argparse
import json
import re
import sys
import time

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

CHALLENGE_MARKS = (
    "just a moment",
    "cf-browser-verification",
    "enable javascript and cookies",
    "attention required",
    "unusual traffic",
    "captcha",
)

MAX_HTML = 6 * 1024 * 1024
MAX_TEXT = 400 * 1024


def guess_charset(resp, raw):
    ct = resp.headers.get("content-type", "") or ""
    m = re.search(r"charset=[\"']?([\w-]+)", ct, re.I)
    if m:
        return m.group(1)
    head = raw[:4096].decode("latin-1", "ignore")
    m = re.search(r"<meta[^>]+charset=[\"']?\s*([\w-]+)", head, re.I)
    if m:
        return m.group(1)
    try:
        return resp.apparent_encoding or "utf-8"
    except Exception:
        return "utf-8"


def looks_challenged(status, html):
    if status in (403, 503, 429):
        low = (html or "")[:6000].lower()
        return any(k in low for k in CHALLENGE_MARKS) or len(low) < 3000
    return False


def via_requests(url):
    import requests

    r = requests.get(url, headers=HEADERS, timeout=(10, 40), allow_redirects=True)
    raw = r.content
    cs = guess_charset(r, raw)
    return {
        "status": r.status_code,
        "final_url": r.url,
        "charset": cs,
        "bytes": len(raw),
        "ctype": r.headers.get("content-type", ""),
        "html": raw[:MAX_HTML].decode(cs, "replace"),
        "via": "requests",
    }


def via_browser(url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        html = page.content()
        status = resp.status if resp else 0
        final_url = page.url
        browser.close()
    return {
        "status": status or 200,
        "final_url": final_url,
        "charset": "utf-8",
        "bytes": len(html.encode("utf-8", "ignore")),
        "ctype": "text/html; charset=utf-8",
        "html": html[:MAX_HTML],
        "via": "chromium",
    }


def to_text(html, base_url):
    """正文抽取：trafilatura 优先，失败退回 readability → 去标签。"""
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html, url=base_url, include_comments=False, include_tables=True,
            favor_recall=True,
        ) or ""
    except Exception as e:
        print(f"  trafilatura 失败: {e}", file=sys.stderr)
    if len(text) < 200:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
            for bad in soup(["script", "style", "noscript", "nav", "footer", "header"]):
                bad.decompose()
            alt = soup.get_text("\n", strip=True)
            if len(alt) > len(text):
                text = alt
        except Exception as e:
            print(f"  bs4 兜底失败: {e}", file=sys.stderr)
    return text[:MAX_TEXT]


def title_of(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--mode", default="html", choices=["html", "text"])
    ap.add_argument("--browser", action="store_true", help="用 Chromium（JS 页 / CF 挑战）")
    ap.add_argument("--out", default="page.json")
    args = ap.parse_args()

    t0 = time.time()
    result = {"url": args.url, "mode": args.mode, "ok": False, "error": ""}

    data = None
    if args.browser:
        try:
            data = via_browser(args.url)
        except Exception as e:
            result["error"] = f"chromium: {e.__class__.__name__}: {str(e)[:160]}"
    if data is None:
        try:
            data = via_requests(args.url)
        except Exception as e:
            result["error"] = f"requests: {e.__class__.__name__}: {str(e)[:160]}"

    # 直连被挑战 → 自动升级到浏览器再试一次
    if data and not args.browser and looks_challenged(data["status"], data["html"]):
        print("  直连疑似被挑战页拦截，升级到 Chromium 重试")
        try:
            alt = via_browser(args.url)
            if not looks_challenged(alt["status"], alt["html"]):
                data = alt
        except Exception as e:
            print(f"  升级失败: {e}", file=sys.stderr)

    if not data:
        result["elapsed"] = round(time.time() - t0, 1)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    result.update(
        ok=True,
        status=data["status"],
        final_url=data["final_url"],
        charset=data["charset"],
        bytes=data["bytes"],
        via=data["via"],
        title=title_of(data["html"]),
        challenged=looks_challenged(data["status"], data["html"]),
        elapsed=round(time.time() - t0, 1),
    )
    if args.mode == "text":
        result["text"] = to_text(data["html"], data["final_url"])
        result["text_len"] = len(result["text"])
    else:
        result["html"] = data["html"]
        result["html_len"] = len(data["html"])

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    brief = {k: v for k, v in result.items() if k not in ("html", "text")}
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
