#!/usr/bin/env python3
"""Google 搜索（GitHub Actions 侧，真浏览器）。

为什么必须放 Actions：Google 对无 JS 的请求一律返回 `enablejs` 脚本拦截页
（2026-10 实测 gbv=1 / udm=14 全部失效，见 VPS 记忆），只有跑 JS 的真浏览器
才拿得到结果；而 VPS 直连被墙、CF Worker 出口被 Google 429。

用法:
  python3 scripts/gh_gsearch.py --q "brake pad NAO" --num 20 --out results.json
"""

import argparse
import json
import sys
import time
import urllib.parse

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const nodes = document.querySelectorAll('#rso a[href], #search a[href]');
  for (const a of nodes) {
    const h3 = a.querySelector('h3');
    if (!h3) continue;
    let href = a.href || '';
    if (!/^https?:/i.test(href)) continue;
    if (href.includes('google.com/search') || href.includes('webcache.googleusercontent')) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    let box = a.closest('div.g') || a.closest('div[data-hveid]');
    if (!box) box = a.parentElement ? a.parentElement.parentElement : null;
    let snippet = box ? (box.innerText || '') : '';
    snippet = snippet.replace(/\s+/g, ' ').trim();
    const title = (h3.innerText || '').trim();
    if (snippet.startsWith(title)) snippet = snippet.slice(title.length).trim();
    out.push({title, href, snippet: snippet.slice(0, 400)});
  }
  return out;
}
"""


def unwrap(href):
    """把 /url?q= 形式的跳转链还原成目标地址。"""
    try:
        sp = urllib.parse.urlsplit(href)
        if sp.path in ("/url", "/url/"):
            q = urllib.parse.parse_qs(sp.query).get("q")
            if q:
                return q[0]
    except Exception:
        pass
    return href


def run_query(page, q, num, hl):
    url = (
        "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)
        + f"&num={num}&hl={hl}&pws=0"
    )
    print(f"  GET {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)

    # 欧盟/英国常见的 cookie 同意页
    for sel in ('#L2AGLb', 'button:has-text("Accept all")', 'button:has-text("I agree")'):
        try:
            loc = page.locator(sel)
            if loc.count():
                loc.first.click(timeout=2500)
                print(f"  点了同意按钮 {sel}")
                page.wait_for_timeout(1200)
                break
        except Exception:
            pass

    try:
        page.wait_for_selector("#rso", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    cur = page.url
    html = page.content()
    if "/sorry/" in cur or "unusual traffic" in html.lower():
        return {"ok": False, "error": "captcha: Google 判定为异常流量（/sorry/）", "items": []}

    items = page.evaluate(EXTRACT_JS) or []
    for it in items:
        it["href"] = unwrap(it.get("href", ""))
    items = [i for i in items if i.get("title") and i.get("href")]

    if not items and "enablejs" in html:
        return {"ok": False, "error": "enablejs: 仍拿到脚本拦截页", "items": []}
    return {"ok": True, "error": "", "items": items, "final_url": cur}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True)
    ap.add_argument("--num", type=int, default=20)
    ap.add_argument("--hl", default="en")
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    t0 = time.time()
    result = {"q": args.q, "ok": False, "error": "", "items": []}

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent=UA,
                locale="en-US",
                viewport={"width": 1366, "height": 900},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"},
            )
            page = ctx.new_page()
            result.update(run_query(page, args.q, args.num, args.hl))
            if not result.get("items") and result.get("ok"):
                print("  结果为空，换 udm=14 再试一次")
                page.goto(
                    "https://www.google.com/search?q=" + urllib.parse.quote_plus(args.q)
                    + f"&num={args.num}&hl={args.hl}&udm=14",
                    wait_until="domcontentloaded", timeout=45000,
                )
                page.wait_for_timeout(2500)
                items = page.evaluate(EXTRACT_JS) or []
                for it in items:
                    it["href"] = unwrap(it.get("href", ""))
                result["items"] = [i for i in items if i.get("title") and i.get("href")]
            browser.close()
    except Exception as e:
        result["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"

    result["count"] = len(result.get("items") or [])
    result["elapsed"] = round(time.time() - t0, 1)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    brief = {k: v for k, v in result.items() if k != "items"}
    print(json.dumps(brief, ensure_ascii=False))
    for i, it in enumerate(result.get("items") or [], 1):
        print(f"  {i:2d}. {it['title'][:70]}  ->  {it['href'][:90]}")

    if result["count"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
