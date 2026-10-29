#!/usr/bin/env python3
"""WAF 站兜底采集：走第三方聚合（Google News RSS / Bing News）。

适用对象：直接访问被 Cloudflare 企业级挑战或 Akamai 拒绝的站点。
思路：不直接打目标站，而是用聚合服务的 RSS ——
      聚合服务自己会去抓那些站，我们只消费它的结果。
      代价是只有标题+摘要（无正文），但足以做情报线索。

用法：
    python scripts/collect_via_aggregator.py

输出：合并进 out/news_overseas.json（与直采结果同一文件，按 url 去重）
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
}

# 聚合检索目标：走 site: 限定，确保结果确实来自该站，
# 避免泛关键词搜索把别的媒体算到它头上（归因不准的老问题）。
# (显示名, 站点域名, 主题限定词)
AGG_TARGETS = [
    ("Counterman",        "counterman.com",        "brake"),
    ("Tire Business",     "tirebusiness.com",      "brake"),
    ("Fleet Owner",       "fleetowner.com",        "brake"),
    ("CCJ Digital",       "ccjdigital.com",        "brake"),
    ("Underhood Service", "underhoodservice.com",  "brake"),
    ("Aftermarket News",  "aftermarketnews.com",   "brake"),
    ("Ratchet+Wrench",    "ratchetandwrench.com",  "brake"),
    ("TMD Friction",      "tmdfriction.com",       "brake pad"),
    ("Brake & Front End", "brakeandfrontend.com",  "brake"),
    ("ImportCar",         "import-car.com",        "brake"),
    ("UNECE WP.29",       "unece.org",             "braking regulation"),
    ("Tire Review",        "tirereview.com",        "brake"),
    ("Auto Service World", "autoserviceworld.com",  "brake"),
    ("Vehicle Service Pros","vehicleservicepros.com","brake"),
    # 2026-10-29 信源扩充：WAF/JS 壳/直连被拦的制动巨头，走聚合兜底
    ("Brembo",            "brembo.com",            "brake"),
    ("Meritor",           "meritor.com",           "brake"),
    ("Bendix",            "bendixvc.com",          "brake"),
    ("Haldex",            "haldex.com",            "brake"),
    ("ZF Aftermarket",    "zf.com",                "brake"),
    ("Hitachi Astemo",    "astemo.com",            "brake"),
    ("Modern Tire Dealer", "moderntiredealer.com", "brake"),
]

# 行业通稿：不限定站点，直接按行业主题检索（补充覆盖面）
AGG_TOPICS = [
    ("行业通稿", "friction material brake pad supplier"),
    ("行业通稿", "brake caliper system OEM news"),
    ("行业通稿", "ECE R90 brake pad regulation"),
    ("行业通稿", "brake pad copper-free regulation"),
    ("行业通稿", "NAO ceramic brake pad market"),
]

# SPA / JS 渲染站：requests 拿不到正文列表，直接走聚合
# (显示名, 域名, 主题词)
AGG_SPA_SITES = [
    ("SAE International", "sae.org",      "brake"),
    ("US EPA",            "epa.gov",      "vehicle brake"),
]

FRICTION_KW = [
    "friction", "brake", "pad", "lining", "tribology", "wear", "rotor", "disc",
    "drum", "caliper", "copper", "ceramic", "nao", "asbestos", "noise", "nvh",
    "regenerative", "r90", "emission", "dust", "formulation", "aftermarket",
    "oem", "automotive", "vehicle", "truck", "fleet", "regulation",
]


def translate(text: str, retry: int = 2) -> str:
    text = (text or "").strip()
    if not text or len(text) > 480:
        return text
    for _ in range(retry + 1):
        try:
            r = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "zh-CN",
                        "dt": "t", "q": text},
                headers=UA, timeout=12)
            if r.status_code == 200:
                data = r.json()
                seg = "".join(p[0] for p in data[0] if p and p[0])
                return seg.strip() or text
        except Exception:
            time.sleep(1.2)
    return text


def _get_with_backoff(url: str, tries: int = 3, base_wait: float = 4.0):
    """带指数退避的 GET。

    Google News RSS 对高频查询会返回 503（临时限流，不是永久封禁），
    退避几秒后重试通常能恢复。不重试的话，定时任务会随机缺数据。
    """
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code == 200:
                return r, None
            last = f"HTTP {r.status_code}"
            # 403/503 才值得退避重试；404 直接放弃
            if r.status_code not in (403, 429, 503):
                return None, last
        except Exception as e:
            last = f"{type(e).__name__}: {repr(e)[:50]}"
        if i < tries - 1:
            time.sleep(base_wait * (i + 1))
    return None, last


def _clean_html(s: str) -> str:
    """RSS description 里的 HTML 片段 -> 纯文本摘要。"""
    if not s:
        return ""
    from html import unescape
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(re.sub(r"\s+", " ", s)).strip()
    return s[:1200]


def gnews_rss(query: str, limit: int = 15):
    """Google News RSS（免费、无需 key）。"""
    url = (f"https://news.google.com/rss/search?q={quote(query)}"
           f"&hl=en-US&gl=US&ceid=US:en")
    r, err = _get_with_backoff(url)
    if r is None:
        return [], err
    try:
        root = ET.fromstring(r.content)
    except Exception as e:
        return [], f"parse: {type(e).__name__}"

    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        if not t or not link:
            continue
        out.append({"title": t, "url": link, "published": pub,
                    "desc": _clean_html(it.findtext("description") or "")})
        if len(out) >= limit:
            break
    return out, None


def bing_rss(query: str, limit: int = 15):
    """Bing News RSS —— Google News 不可达时的备用通道。"""
    url = (f"https://www.bing.com/news/search?q={quote(query)}"
           f"&format=RSS&count={limit}")
    r, err = _get_with_backoff(url, tries=2, base_wait=3.0)
    if r is None:
        return [], err
    try:
        root = ET.fromstring(r.content)
    except Exception as e:
        return [], f"parse: {type(e).__name__}"

    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        link = unwrap_url((it.findtext("link") or "").strip())
        if not t or not link:
            continue
        out.append({"title": t, "url": link,
                    "published": (it.findtext("pubDate") or "").strip(),
                    "desc": _clean_html(it.findtext("description") or "")})
        if len(out) >= limit:
            break
    return out, None


def unwrap_url(link: str) -> str:
    """解 Bing 的 apiclick 跳转包装，拿到真实媒体直链。

    Bing News RSS 多数直接给真实链接，但部分条目会裹成
    bing.com/news/apiclick.aspx?...&url=<真实URL>，这里把 url 参数取出来。
    """
    if not link or "apiclick" not in link:
        return link
    try:
        from urllib.parse import urlsplit, parse_qs, unquote
        q = parse_qs(urlsplit(link).query)
        if "url" in q:
            return unquote(q["url"][0])
    except Exception:
        pass
    return link


def fetch_aggregated(query: str, limit: int = 15):
    """Bing 优先 → Google 兜底。都失败返回 ([], 原因)。

    为什么换顺序（2026-10-01）：
    Google News RSS 的 <link> 是 news.google.com/rss/articles/<加密串> 中转链，
    靠 Google 非公开 batchexecute 接口解码实测 270/270 全失败（接口已变），
    导致聚合条目点不回真实媒体页，与「每条都有真实来源链接」目标不符。
    Bing News RSS 多数直接给真实媒体直链；Google 仍保留为 Bing 不可达时的兜底。
    """
    items, err_b = bing_rss(query, limit)
    if items:
        return items, None, "bing-news-rss"
    items, err_g = gnews_rss(query, limit)
    if items:
        return items, None, "google-news-rss"
    return [], f"bing={err_b}; google={err_g}", None


def main():
    out_path = "out/news_overseas.json"
    os.makedirs("out", exist_ok=True)
    try:
        with open(out_path, encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        store = {}
    articles = {a["url"]: a for a in store.get("articles", []) if a.get("url")}

    # 组装检索任务：(显示名, 站点域名 or None, 查询串)
    tasks = [(name, dom, f"site:{dom} {kw}") for name, dom, kw in AGG_TARGETS]
    tasks += [(name, dom, f"site:{dom} {kw}") for name, dom, kw in AGG_SPA_SITES]
    tasks += [(name, None, kw) for name, kw in AGG_TOPICS]

    stats, errors = [], []
    for i, (name, dom, query) in enumerate(tasks):
        items, err, via = fetch_aggregated(query)
        label = f"{name}(聚合)"
        if err:
            errors.append({"source": label, "error": err})
            print(f"  ⛔ {name:<20} {err[:80]}")
            continue

        kept = 0
        for it in items:
            # 站点限定任务：标题或链接里必须出现该域名，确保归因正确
            if dom:
                blob = f"{it['title']} {it['url']}".lower()
                if dom.lower() not in blob:
                    continue
            low = it["title"].lower()
            if not any(k in low for k in FRICTION_KW):
                continue
            # 聚合结果标题末尾常带 " - 媒体名"，保留原样便于溯源
            # 入库 url 始终存真实媒体直链（Bing 已解包），便于存量 enrich 按 url 回填正文。
            # 展示层「可点击外链」由模板 orig_link 过滤器负责（news.google.com 中转链/搜索页
            # 自动转 DDG 搜索），不要在此覆盖成 DDG 链，否则 url 字段与 enrich 真链不一致、回填失效。
            rec = {
                "title": it["title"],
                "url": it["url"],
                "source": label,
                "region": "海外",
                "tag": "聚合来源",
                "lang": "en",
                "level": "headline",       # 标记：仅标题级（无正文时）
                "desc": (it.get("desc") or "")[:3000],
                "via": via,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            # 站点限定任务（site:域名）已知真实域名，落库供前端展示
            # 「来源：媒体名 · 真实域名」，即使 URL 仍是跳转链也能显示来源。
            if dom:
                rec["source_domain"] = dom
            zh = translate(it["title"])
            if zh and zh != it["title"]:
                rec["title_zh"] = zh
            articles[it["url"]] = rec
            kept += 1
        stats.append({"source": name, "via": via,
                      "fetched": len(items), "kept": kept})
        print(f"  ✅ {name:<20} [{via}] 取 {len(items):>3} 条，相关 {kept:>3} 条")
        # 任务间节流：Google News 对密集查询敏感，放慢以降低 503 概率
        if i < len(tasks) - 1:
            time.sleep(2.0)

    store["articles"] = sorted(articles.values(),
                               key=lambda x: x["collected_at"], reverse=True)
    store["count"] = len(articles)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    store.setdefault("stats", []).extend(stats)
    store.setdefault("errors", []).extend(errors)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    print(f"\n合并后共 {len(articles)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
