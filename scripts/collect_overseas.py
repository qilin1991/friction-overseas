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
    # EPA 用 Drupal：新闻详情页是 /newsreleases/<region>/<slug>
    # 或 /node/<id>（旧式），两者都要能匹配；排除频道页与静态资源。
    {"name": "US EPA", "list_url": "https://www.epa.gov/newsreleases",
     "link_re": r"epa\.gov/(?:newsreleases/[a-z0-9\-]+/[a-z0-9\-]{6,}|node/\d+)",
     "exclude_url": r"epa\.gov/(?:newsroom/|themes/|modules/|sites/|about|"
                    r"environmental-topics|laws-regulations|newsreleases/?$)",
     "base": "https://www.epa.gov", "region": "海外", "tag": "法规", "translate": True},

    # ===== 探针通过：Azure IP 可访问的（用精确列表页规则提升命中率）=====
    # 注意：这些站是 Informa / Babcox / Endeavor 系 CMS，
    #       文章 URL 形如 /<channel>/<slug>-<id> 或 /<slug>/，需排除栏目/标签/作者页。
    # exclude  : 命中即丢弃的路径片段（栏目、tag、author、page 等）
    {"name": "Counterman", "list_url": "https://www.counterman.com/",
     "list_url2": "https://www.counterman.com/category/brakes/",
     "link_re": r"counterman\.com/(?!(?:tag|author|category|page|about|contact|subscribe)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.counterman.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "Fleet Owner", "list_url": "https://www.fleetowner.com/",
     "list_url2": "https://www.fleetowner.com/equipment/",
     "link_re": r"fleetowner\.com/(?!(?:tag|author|category|page|about|contact|subscribe)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.fleetowner.com", "region": "海外", "tag": "商用车", "translate": True},
    {"name": "Underhood Service", "list_url": "https://www.underhoodservice.com/",
     "list_url2": "https://www.underhoodservice.com/category/brakes/",
     "link_re": r"underhoodservice\.com/(?!(?:tag|author|category|page|about|contact|subscribe)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.underhoodservice.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "Aftermarket News", "list_url": "https://www.aftermarketnews.com/",
     "list_url2": "https://www.aftermarketnews.com/category/breaking-news/",
     "link_re": r"aftermarketnews\.com/(?!(?:tag|author|category|page|about|contact|subscribe)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.aftermarketnews.com", "region": "海外", "tag": "售后市场", "translate": True},

    # ===== 直连被拦：保留配置，探针若哪天通过即自动生效 =====
    {"name": "Tire Business", "list_url": "https://www.tirebusiness.com/",
     "link_re": r"tirebusiness\.com/(?!(?:tag|author|category|page)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.tirebusiness.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "CCJ Digital", "list_url": "https://www.ccjdigital.com/",
     "link_re": r"ccjdigital\.com/(?!(?:tag|author|category|page)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.ccjdigital.com", "region": "海外", "tag": "商用车", "translate": True},
    {"name": "TMD Friction", "list_url": "https://www.tmdfriction.com/en/news/",
     "link_re": r"tmdfriction\.com/en/news/[a-z0-9\-]{6,}",
     "base": "https://www.tmdfriction.com", "region": "海外", "tag": "摩擦材料", "translate": True},

    # ===== 2026-10-01 信源普查新增：Azure IP 实测可直连 =====
    {"name": "Miba AG", "list_url": "https://www.miba.com/en/news",
     "link_re": r"miba\.com/en/news/(?!(?:archive|filter)/)[a-z0-9\-_]{6,}",
     "base": "https://www.miba.com", "region": "海外", "tag": "摩擦材料", "translate": True},
    # Informa 系 CMS：真实文章 URL 形如 /home/article/<8位数字ID>/<slug>
    # 首页只挂 1 条（其余靠 JS 轮播），必须走列表页才有量。
    {"name": "Ratchet+Wrench", "list_url": "https://www.ratchetandwrench.com/home/latest",
     "list_url2": "https://www.ratchetandwrench.com/",
     "link_re": r"ratchetandwrench\.com/(?:home/)?article/\d{6,}/[a-z0-9\-]{4,}",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.ratchetandwrench.com", "region": "海外", "tag": "售后技术", "translate": True},
    {"name": "Tomorrow's Technician", "list_url": "https://www.tomorrowstechnician.com/",
     "list_url2": "https://www.tomorrowstechnician.com/category/brakes/",
     "link_re": r"tomorrowstechnician\.com/(?!(?:tag|author|category|page|about)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.tomorrowstechnician.com", "region": "海外", "tag": "售后技术", "translate": True},
    {"name": "Brake & Front End", "list_url": "https://www.brakeandfrontend.com/",
     "list_url2": "https://www.brakeandfrontend.com/category/brakes/",
     "link_re": r"brakeandfrontend\.com/(?!(?:tag|author|category|page|about)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.brakeandfrontend.com", "region": "海外", "tag": "制动系统", "translate": True},
    {"name": "ImportCar", "list_url": "https://www.import-car.com/",
     "link_re": r"import-car\.com/(?!(?:tag|author|category|page|about)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.import-car.com", "region": "海外", "tag": "售后技术", "translate": True},
    # WordPress 日期式 URL：/YYYY/MM/DD/slug/  —— 旧规则（纯 slug）匹配不到，已修正
    {"name": "Repairer Driven News", "list_url": "https://www.repairerdrivennews.com/",
     "link_re": r"repairerdrivennews\.com/\d{4}/\d{2}/\d{2}/[a-z0-9\-]{8,}/?$",
     "base": "https://www.repairerdrivennews.com", "region": "海外", "tag": "售后市场", "translate": True},
    {"name": "BodyShop Business", "list_url": "https://www.bodyshopbusiness.com/",
     "link_re": r"bodyshopbusiness\.com/(?!(?:tag|author|category|page|about)/)[a-z0-9\-]{12,}/?$",
     "exclude_url": r"/(?:tag|tags|author|authors|category|categories|page|topics|topic|search|about|contact|advertise|subscribe|newsletter|events|webinars|podcast|video|whitepapers|resources|jobs|privacy|terms|sitemap)(?:/|$)",
     "base": "https://www.bodyshopbusiness.com", "region": "海外", "tag": "售后市场", "translate": True},
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


# ── 精选评分 + 聚簇（对标 AIHOT 的「精选评分流程 / 聚簇机制」）────────
# 旧版只有 is_relevant 关键词硬过滤，好差新闻混排、跨站同事件重复。
# 这里补两层：① 加权评分排序 ② 跨站同事件归并成簇（取代表源+站点数）。
STOP = set(
    "the a an of to in on for and or with new from at by is are be this that it as "
    "up out over after before its their our your his her will would can could via "
    "how what when why who which show shows says said into about than then they we you"
    .split()
)

# 来源权威度：垂直站/原厂/标准组织权重高，聚合通稿权重低
SOURCE_WEIGHT = {
    "SAE International": 1.0, "US EPA": 0.95, "Bosch 新闻室": 0.9,
    "Miba AG": 0.9, "TMD Friction": 0.9, "Brembo": 0.9,
    "Counterman": 0.7, "Aftermarket News": 0.7, "Brake & Front End": 0.7,
    "ImportCar": 0.7, "Underhood Service": 0.7, "Tomorrow's Technician": 0.7,
    "BodyShop Business": 0.7, "Fleet Owner": 0.7, "Repairer Driven News": 0.7,
    "Ratchet+Wrench": 0.7, "CCJ Digital": 0.65, "Tire Business": 0.65,
    "行业通稿": 0.3,
}


def _title_tokens(title: str) -> set:
    """英文标题轻量分词：去停用词，保留术语与实体。"""
    toks = re.findall(r"[a-z0-9][a-z0-9'\-\.]{2,}", (title or "").lower())
    return {t for t in toks if t not in STOP}


def score_article(rec: dict) -> float:
    """加权评分（0~5 区间，便于 VPS 排序 + 阈值过滤）。

    三因子：
      · 摩擦相关度  —— 标题命中 FRICTION_KW 的术语数（每个 +1.2）
      · 来源权威度  —— SOURCE_WEIGHT 映射（×1.5）
      · 时效性      —— 越新越高（30 天内线性衰减，×1.0）
    """
    t = (rec.get("title") or "").lower()
    rel = sum(1.2 for k in FRICTION_KW if k in t)
    # 标题命中的强实体（品牌/车型/法规号）额外加权
    src_w = SOURCE_WEIGHT.get(rec.get("source"), 0.6)
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(rec["collected_at"])).days
        fresh = max(0.0, 1.0 - age / 30.0)
    except Exception:
        fresh = 0.5
    return round(rel * 1.0 + src_w * 1.5 + fresh * 1.0, 3)


def cluster_articles(articles: list, th: float = 0.34) -> list:
    """跨站同事件归并（聚簇）。

    指纹 = 标题术语集合 ∪ 命中的摩擦关键词。两文指纹 Jaccard ≥ th 即同簇。
    贪心并查集合并；簇内取「标题最长」一条作代表，输出跨站站点数与代表 URL。

    幂等：同输入必得同输出（并查集 + 排序稳定），便于 VPS 侧按 event_key 去重。
    """
    recs = []
    for a in articles:
        if not a.get("url"):
            continue
        kw = {k for k in FRICTION_KW if k in (a.get("title") or "").lower()}
        fp = kw | _title_tokens(a.get("title") or "")
        if fp:
            recs.append((a, fp))

    n = len(recs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            a, b = recs[i][1], recs[j][1]
            uni = len(a | b)
            if uni and len(a & b) / uni >= th:
                union(i, j)

    groups = {}
    for i, (a, _fp) in enumerate(recs):
        groups.setdefault(find(i), []).append(a)

    clusters = []
    for mem in groups.values():
        if len(mem) < 2:
            continue  # 单条不成簇，仍作为独立文章在 articles 里展示
        sources = sorted({m.get("source") for m in mem if m.get("source")})
        rep = max(mem, key=lambda m: len(m.get("title", "")))
        clusters.append({
            "title": rep.get("title"),
            "sources": sources,
            "count": len(mem),
            "urls": [m.get("url") for m in mem if m.get("url")],
            "representative_url": rep.get("url"),
        })
    clusters.sort(key=lambda c: (-c["count"], c["title"] or ""))
    return clusters


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


def _extract_from(soup, pat, base: str, limit: int, exclude=None) -> list:
    """从一页 soup 里抽文章链接。

    ⚠️ 关键点：**先把 href 展开成绝对 URL 再匹配正则**。
    早期版本直接拿 href 去 search，而页面里的链接常常是相对路径
    （如 "/emissions-efficiency"），而 link_re 是按完整域名写的
    （如 r"fleetowner\\.com/..."），于是相对路径全部匹配失败，
    表现为「入口可达 links=N hit=0」——看着像规则失效，其实只是
    没做 urljoin。2026-10-01 修：Fleet Owner / Miba / US EPA 都栽在这。

    exclude：命中该正则的 URL 直接丢弃（用于排除频道页 / 静态资源）。
    """
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        # 展开成绝对 URL 之后再匹配
        full = href if href.startswith("http") else urljoin(base + "/",
                                                           href.lstrip("/"))
        if not pat.search(full):
            continue
        if exclude is not None and exclude.search(full):
            continue

        title = a.get_text(" ", strip=True)
        if not title:
            img = a.find("img")
            title = (img.get("alt") if img else "") or a.get("title", "")
        title = re.sub(r"\s+", " ", title.lstrip("·•-—|>》 \t")).strip()
        if len(title) < 10:
            continue

        # 去掉 query / fragment，避免同一文章多种形式重复
        url = full.split("#")[0].split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url})
        if len(items) >= limit:
            break
    return items


def fetch_source(src: dict, limit: int = 20) -> tuple:
    """抓一个源，返回 (items, error)。

    支持多入口：list_url（主）+ list_url2（备用/专题栏目）。
    主入口失败或命中过少时，自动补试备用入口 —— 这对
    「首页只有少量文章、专题栏目才密集」的 CMS 很关键。
    """
    entry_points = [u for u in (src.get("list_url"), src.get("list_url2")) if u]
    pat = re.compile(src["link_re"], re.I)
    # 排除规则：站点导航/栏目页/静态资源等「非文章」路径，防止正则放宽后混入杂链接
    _exc = src.get("exclude_url")
    exc_pat = re.compile(_exc, re.I) if _exc else None
    items, seen = [], set()
    last_err = None
    diag = []  # 每个入口的诊断：HTTP 码 + 页面链接数 + 正则命中数

    for idx, url in enumerate(entry_points):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code} @ {url}"
                diag.append(f"{url} -> HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.content, "html.parser")
        except Exception as e:
            last_err = f"{type(e).__name__}: {repr(e)[:70]} @ {url}"
            diag.append(f"{url} -> {type(e).__name__}")
            continue

        got = _extract_from(soup, pat, src["base"], limit,
                            exclude=exc_pat)
        # 诊断：页面共有多少站内链接、正则命中多少
        n_links = len([a for a in soup.find_all("a", href=True)])
        host = src["base"].split("//")[-1].split("/")[0]
        # 收集站内链接的真实路径样本，便于反推正确的正则（关键排查手段）
        sample = []
        for a in soup.find_all("a", href=True):
            h = a["href"].split("#")[0].split("?")[0]
            if host in h and h not in sample:
                sample.append(h)
            if len(sample) >= 12:
                break
        diag.append(f"{url} -> HTTP200 links={n_links} hit={len(got)}")
        if not got and sample:
            diag.append("   路径样本: " + " | ".join(s[:70] for s in sample[:8]))
        for it in got:
            if it["url"] not in seen:
                seen.add(it["url"])
                items.append(it)
        # 主入口已拿到足量，不再试备用
        if idx == 0 and len(items) >= limit // 2:
            break
        time.sleep(0.6)

    if not items and last_err:
        return [], last_err + " | " + " ; ".join(diag)
    if not items:
        # 入口全通但正则一条没匹配上 —— 这是「规则失效」而非「网络不通」，要能区分
        return [], "入口可达但正则 0 命中 | " + " ; ".join(diag)
    return items[:limit], None


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

    # ── 精选评分 + 聚簇（对标 AIHOT）──
    # 先给每条打加权分，再跨站聚簇；两条独立产出，VPS 侧各取所需。
    for a in articles.values():
        a["score"] = score_article(a)
    clusters = cluster_articles(list(articles.values()))
    print(f"  🔗 聚簇：{len(clusters)} 个跨站事件（涉及 "
          f"{sum(c['count'] for c in clusters)} 条重复文章）")

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "friction-actions/collect_overseas.py",
        "count": len(articles),
        "articles": sorted(articles.values(),
                           key=lambda x: (x.get("score", 0), x["collected_at"]),
                           reverse=True),
        "clusters": clusters,
        "stats": stats,
        "errors": errors,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n合计 {len(articles)} 条 → {out_path}；失败源 {len(errors)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
