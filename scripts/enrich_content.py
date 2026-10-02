#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量补正文：抓 news_overseas.json 里仅标题条目的原文正文。

背景：历史聚合/直采条目多数只存了标题（content_level=headline），
VPS 出口被 WAF 拦抓不到原文，本脚本跑在 Actions（Azure 出口）补抓。

输出 out/news_content.json（{url: text}）→ workflow 提交到 data/ →
VPS 合并端 fetch_friction_overseas.py 按 url 回填 article 表。

策略：
- 跳过聚合中转/搜索链（news.google.com / bing.com / duckduckgo.com）
- 增量：news_content.json 已有的 url 不重抓
- 直连优先；直连被 WAF 拦截/正文不足时，回落 r.jina.ai 阅读器代理兜底
  （Actions 在 Azure 国际出口，可访问 jina，绕过源站 WAF）
- 每轮限量 ENRICH_LIMIT（默认 250 条），workflow 定期调用逐步补齐
- 失败/空正文记 FAILS（连续 2 轮失败自动放弃，避免反复打 WAF）
"""
import json
import os
import re
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
}
SKIP_HOSTS = ("news.google.com", "bing.com", "duckduckgo.com")
JINA = "https://r.jina.ai/"
LIMIT = int(os.environ.get("ENRICH_LIMIT", "250"))
IN_PATH = "out/news_overseas.json"
OUT_PATH = "out/news_content.json"

# jina 返回首部元数据行（Title/Published/URL/Image 等），正文前需剥掉
_META_PREFIX = (
    "title:", "published:", "url:", "image:", "description:",
    "total tokens:", "warning:",
)


def _clean_jina(text: str) -> str:
    """剥掉 jina 阅读器返回的首部元数据，保留正文 Markdown。"""
    lines = text.splitlines()
    out, in_meta = [], True
    for ln in lines:
        s = ln.strip()
        if in_meta:
            low = s.lower()
            if any(low.startswith(p) for p in _META_PREFIX):
                continue
            if s.startswith("http://") or s.startswith("https://"):
                continue
            # 元数据区空行或极短行跳过
            if not s or len(s) < 30:
                if not out:
                    continue
            in_meta = False
        out.append(ln)
    return "\n".join(out).strip()


def _direct(url: str, limit: int) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=18)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            tag.decompose()
        art = soup.find("article") or soup.find("main") or soup
        paras, total = [], 0
        for el in art.find_all(["p", "h2", "li"]):
            t = el.get_text(" ", strip=True)
            if len(t) >= 40:
                paras.append(t)
                total += len(t)
            if total > limit:
                break
        return "\n\n".join(paras)[:limit]
    except Exception:
        return ""


def fetch_text(url: str, limit: int = 6000) -> str:
    # 1) 直连抓正文
    txt = _direct(url, limit)
    if len(txt) >= 200:
        return txt
    # 2) jina 阅读器代理兜底（绕过 WAF），仅当直连不足时
    try:
        r = requests.get(JINA + url, headers=UA, timeout=22)
        if r.status_code == 200:
            t = _clean_jina(r.text)
            if len(t) >= 200:
                return t[:limit]
    except Exception:
        pass
    return ""


def main():
    try:
        with open(IN_PATH, encoding="utf-8") as f:
            rows = json.load(f).get("articles", [])
    except Exception:
        rows = []
    # 补充：存量仅标题队列（VPS 导出到 data/enrich_queue.json，按 url 补齐历史条目）
    try:
        with open("data/enrich_queue.json", encoding="utf-8") as f:
            rows += json.load(f)
    except Exception:
        pass
    if not rows:
        print("no source urls")
        return 0

    store, fails = {}, {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                d = json.load(f)
            store = d.get("contents", {})
            fails = d.get("fails", {})
        except Exception:
            pass

    done = ok = 0
    for a in rows:
        if done >= LIMIT:
            break
        url = (a.get("url") or "").strip()
        if not url or url in store:
            continue
        host = urlsplit(url).hostname or ""
        if any(h in host for h in SKIP_HOSTS):
            continue
        prev_fail = fails.get(url, 0)
        if prev_fail >= 2:
            continue
        text = fetch_text(url)
        if len(text) >= 200:
            store[url] = text
            fails.pop(url, None)
            ok += 1
        else:
            fails[url] = prev_fail + 1
        done += 1
        time.sleep(0.4)

    os.makedirs("out", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"contents": store, "fails": fails}, f, ensure_ascii=False)
    print(f"enrich: 尝试 {done}，成功 {ok}，累计正文 {len(store)} 条，累计失败 {len(fails)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
