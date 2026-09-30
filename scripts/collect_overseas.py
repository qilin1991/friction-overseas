#!/usr/bin/env python3
"""海外信源采集（跑在 GitHub Actions / Azure 机房）。

做三件事：
  1. 按站点配置抓列表页，抽出「标题 + 链接」；
  2. 可选：用 Google 免费翻译把英文标题译成中文（无 API key）；
  3. 归并进 out/news_overseas.json，供 VPS 定时拉取合并。

设计原则（与 friction-site 保持一致）：
  - 单站失败不影响整体：每站独立 try，失败记入 errors 继续跑；
  - 结果去重：按 url 归并，保留首次出现；
  - 幂等：同一天重复跑不会产生重复条目（按 url 去重 + updated_at 覆盖）。
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

# ── 源配置 ──────────────────────────────────────────────────────────
# list_url : 列表页
# link_re  : 文章链接正则（相对或绝对都能匹配）
# region   : 海外 / 国内
# tag      : 归入 friction 的哪个分类（写入 JSON，VPS 侧可再次过滤）
# translate: 是否翻译标题
SOURCES = [
    # ===== 已验证可从 Azure 访问的（对照组）=====
    {"name": "SAE International", "list_url": "https://www.sae.org/news",
     "link_re": r"/news/\d{4}/\d{2}/\d{2}/|/news/[a-z0-9\-]{8,}",
     "base": "https://www.sae.org", "region": "海外", "tag": "标准/工程", "translate": True},
    {"name": "Bosch 新闻室", "list_url": "https://www.bosch-presse.de/pressportal/de/en/",
     "link_re": r"/pressportal/de/en/[a-z0-9\-_]{10,}\.html",
     "base": "https://www.bosch-presse.de", "region": "海外", "tag": "制动系统", "translate": True},
    {"name": "US EPA", "list_url": "https://www.epa.gov/newsreleases",
     "link_re": r"epa\.gov/newsreleases/(?!search)[a-z0-9\-]+/[a-z0-9\-]{6,}",
     "base": "https://www.epa.gov", "region": "海外", "tag": "法规", "translate": True},

    # ===== 待验证：VPS 上被 WAF 拦的（探针通过则自动收）=====
    {"name": "Counterman", "list_url": "https://www.counterman.com/",
     "link_re": r"counterman\.com/[a-z0-9\-]{8,}/?$",
     "base": "https://www.counterman.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "Tire Business", "list_url": "https://www.tirebusiness.com/",
     "link_re": r"tirebusiness\.com/[a-z0-9\-]{8,}",
     "base": "https://www.tirebusiness.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "Fleet Owner", "list_url": "https://www.fleetowner.com/",
     "link_re": r"fleetowner\.com/[a-z0-9\-]{8,}",
     "base": "https://www.fleetowner.com", "region": "海外", "tag": "商用车", "translate": True},
    {"name": "CCJ Digital", "list_url": "https://www.ccjdigital.com/",
     "link_re": r"ccjdigital\.com/[a-z0-9\-]{8,}",
     "base": "https://www.ccjdigital.com", "region": "海外", "tag": "商用车", "translate": True},
    {"name": "Underhood Service", "list_url": "https://www.underhoodservice.com/",
     "link_re": r"underhoodservice\.com/[a-z0-9\-]{8,}",
     "base": "https://www.underhoodservice.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "Aftermarket News", "list_url": "https://www.aftermarketnews.com/",
     "link_re": r"aftermarketnews\.com/[a-z0-9\-]{8,}",
     "base": "https://www.aftermarketnews.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "TMD Friction", "list_url": "https://www.tmdfriction.com/en/news",
     "link_re": r"tmdfriction\.com/[a-z0-9\-/]{8,}",
     "base": "https://www.tmdfriction.com", "region": "海外", "tag": "摩擦材料", "translate": True},
]

# 摩擦相关性过滤（与 friction-site 的 FRICTION_KEYWORDS 对齐）
FRICTION_KW = [
    "friction", "brake", "pad", "lining", "tribology", "wear", "rotor", "disc",
    "drum", "caliper", "copper", "ceramic", "nao", "semi-metallic", "asbestos",
    "noise", "nvh", "regenerative", "ebc", "r90", "emission", "dust", "formulation",
    "substrate", "shim", "backing plate", "commercial vehicle", "heavy-duty",
    "aftermarket", "oem", "automotive", "vehicle", "truck", "fleet",
]


def is_relevant(title: str, content: str = "") -> bool:
    t = f"{title} {content}".lower()
    return any(k in t for k in FRICTION_KW)


def translate(text: str, retry: int = 2) -> str:
    """Google 免费翻译端点（无 key）。失败原样返回，绝不阻塞。"""
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


def fetch_source(src: dict, limit: int = 20) -> tuple:
    """抓一个源，返回 (items, error)。"""
    try:
        r = requests.get(src["list_url"], headers=UA, timeout=25)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        soup = BeautifulSoup(r.content, "html.parser")
    except Exception as e:
        return [], f"{type(e).__name__}: {repr(e)[:80]}"

    pat = re.compile(src["link_re"], re.I)
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if not pat.search(href):
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            img = a.find("img")
            title = (img.get("alt") if img else "") or a.get("title", "")
        title = re.sub(r"\s+", " ", title.lstrip("·•-—|>》 \t")).strip()
        if len(title) < 10:
            continue
        url = href if href.startswith("http") else urljoin(src["base"] + "/", href.lstrip("/"))
        if url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url})
        if len(items) >= limit:
            break
    return items, None


def load_existing(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}


def main():
    out_path = "out/news_overseas.json"
    os.makedirs("out", exist_ok=True)
    store = load_existing(out_path)
    articles = {a["url"]: a for a in store.get("articles", []) if a.get("url")}

    stats, errors = [], []
    for src in SOURCES:
        items, err = fetch_source(src)
        if err:
            errors.append({"source": src["name"], "error": err})
            print(f"  ⛔ {src['name']:<20} {err}")
            continue
        kept = 0
        for it in items:
            if not is_relevant(it["title"]):
                continue
            rec = {
                "title": it["title"],
                "url": it["url"],
                "source": src["name"],
                "region": src["region"],
                "tag": src["tag"],
                "lang": "en",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            if src.get("translate"):
                zh = translate(it["title"])
                if zh and zh != it["title"]:
                    rec["title_zh"] = zh
            articles[it["url"]] = rec     # 同 url 覆盖 = 幂等
            kept += 1
        stats.append({"source": src["name"], "fetched": len(items), "kept": kept})
        print(f"  ✅ {src['name']:<20} 抓 {len(items):>3} 条，相关 {kept:>3} 条")

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "friction-actions/collect_overseas.py",
        "count": len(articles),
        "articles": sorted(articles.values(), key=lambda x: x["collected_at"], reverse=True),
        "stats": stats,
        "errors": errors,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n合计 {len(articles)} 条 → {out_path}；失败源 {len(errors)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
