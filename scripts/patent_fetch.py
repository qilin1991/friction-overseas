# -*- coding: utf-8 -*-
"""Google Patents 国外专利抓取（跑在 GitHub Actions，Azure 出口）。

背景：本机 + VPS 到 patents.google.com 全不可达；Espacenet 403（连 GH 出口也拦）；
OPS 无 key 403（Fair Use）；PatentsView 在 GH 出口连 DNS 都不解析。
→ Google Patents XHR 是唯一无 key、可批量、覆盖 US/EP/JP/WO/KR/DE 的通道。

已知结构（2026-10-21 实测）：
  GET https://patents.google.com/xhr/query?url=q%3D<quote_plus(query)>
  → {"results": {"cluster": [ {"result": [ {"id": "patent/US10302165B2/en",
        "rank": 0, "patent": {"title","snippet","priority_date","filing_date",
        "grant_date","publication_date","inventor","assignee",
        "publication_number","language","pdf","family_metadata"} } ], ...} ],
        "total_num_results": N, ...}}
  🔴 cluster 下的每个元素不是一条专利，而是一组**相似文档**（`result` 是 list）→
     必须两层展开，别当成 dict 直接 .get（会 AttributeError）。

用法（Actions 内）：
  python scripts/patent_fetch.py --pages 3 --detail 1 [--queries "a,b,c"]
                                 [--country US,JP,EP,WO,KR,DE] [--out data/patent_google.jsonl]
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# 🔴 检索词必须是「引号精确短语 + 自由词」：实测裸长 query（如
#    "non-asbestos friction material composition" 不加引号）配 country: 时
#    排序会崩 —— 首条变成 "Engineered fuel feed stock"。
#    加引号后 total 从 48927 收敛到 40/278，且前 10 条全命中。
QUERIES = [
    '"friction material composition"',
    '"non-asbestos friction material"',
    '"brake pad" "friction material"',
    '"friction material" "weight percent"',
    '"brake lining" composition',
    '"friction material" "phenolic resin"',
    '"brake block" "friction material"',
    '"friction material" binder fiber filler',
    '"NAO" "friction material"',
    '"friction material" copper free',
    '"friction material" ceramic fiber',
    '"disc brake pad" composition',
    '"friction material" "parts by weight"',
    '"brake friction material" aramid',
]
COUNTRIES = ["US", "EP", "JP", "WO", "KR", "DE", "FR", "GB", "IT", "CN"]

# 配方/组成语义（决定是否抓详情页）
FORM = re.compile(r"composition|formulation|weight percent|wt\s?%|by weight|"
                  r"parts by weight|mixture|composite|配方|组合物", re.I)

# 相关性硬闸门：标题或摘要必须落在摩擦/制动语义内（剔除排序崩坏时的噪声）
FRIC = re.compile(r"friction|brake|braking|tribolog|clutch|lining|pad\b|"
                  r"摩擦|制动|刹车", re.I)


def fetch(url, timeout=40, headers=None):
    h = {"User-Agent": UA, "Accept": "text/html,application/json,*/*",
         "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, headers=h)
    for a in range(3):
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(2 * (a + 1))
                continue
            return e.code, e.read()
        except Exception as e:  # noqa: BLE001
            if a == 2:
                return 0, ("ERR " + repr(e)).encode()
            time.sleep(1.5 * (a + 1))
    return 0, b""


def xhr_url(q, extra=""):
    inner = "q=" + urllib.parse.quote_plus(q) + extra
    return "https://patents.google.com/xhr/query?url=" + urllib.parse.quote(inner, safe="")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def flatten(j):
    """两层展开：results.cluster[] → result[] → patent{}"""
    out = []
    for c in ((j.get("results") or {}).get("cluster") or []):
        for r in (c.get("result") or []) if isinstance(c, dict) else []:
            p = r.get("patent") or {}
            if not p.get("publication_number"):
                continue
            out.append(dict(
                pn=p["publication_number"],
                title=clean(p.get("title")),
                snippet=clean(p.get("snippet")),
                assignee=clean(p.get("assignee")),
                inventor=clean(p.get("inventor")),
                priority_date=p.get("priority_date") or "",
                filing_date=p.get("filing_date") or "",
                grant_date=p.get("grant_date") or "",
                publication_date=p.get("publication_date") or "",
                language=p.get("language") or "",
                url="https://patents.google.com/patent/%s/en" % p["publication_number"],
                countries=[x.get("country_code") for x in
                           (((p.get("family_metadata") or {}).get("aggregated") or {})
                            .get("country_status") or [])],
            ))
    return out


def first_pn(q, extra=""):
    st, b = fetch(xhr_url(q, extra), headers={"Accept": "application/json"})
    try:
        items = flatten(json.loads(b.decode("utf-8", "replace")))
        return st, (items[0]["pn"] if items else "")
    except Exception:  # noqa: BLE001
        return st, ""


def detect_pager(q):
    """试出翻页写法：返回能让第一条变化的 extra 模板。"""
    _, base = first_pn(q)
    for tmpl in ("&page=%d", "&start=%d", "&oq=%d"):
        _, pn1 = first_pn(q, tmpl % 1)
        if pn1 and pn1 != base:
            return tmpl
    return ""


def count_items(q, extra=""):
    st, b = fetch(xhr_url(q, extra), headers={"Accept": "application/json"})
    try:
        return len(flatten(json.loads(b.decode("utf-8", "replace"))))
    except Exception:  # noqa: BLE001
        return 0


def detect_num(q):
    """试出每页条数写法：Google Patents 搜索页支持 num=（默认 10）。"""
    base = count_items(q)
    for cand in ("&num=100", "&num=50", "&num=25"):
        n = count_items(q, cand)
        if n > base:
            return cand
    return ""


def detail(pn):
    """详情页：摘要 + 权利要求片段（配方常写重量百分比）。"""
    st, b = fetch("https://patents.google.com/patent/%s/en" % pn)
    if st != 200:
        return {}
    txt = b.decode("utf-8", "replace")
    ab = ""
    m = re.search(r'<section itemprop="description"[^>]*>([\s\S]{0,4000}?)</section>', txt)
    if m:
        ab = clean(re.sub(r"<[^>]+>", " ", m.group(1)))
    cl = ""
    m2 = re.search(r'itemprop="claims"[^>]*>([\s\S]{0,6000}?)</section>', txt)
    if m2:
        cl = clean(re.sub(r"<[^>]+>", " ", m2.group(1)))
    ass = re.findall(r'<meta name="DC.contributor"[^>]*content="([^"]{1,80})"', txt)
    return {"abstract": ab[:1800], "claims": cl[:1800], "meta_assignee": ass[:3]}


def base_key(pn):
    """US10626934B2 / US10626934A1 视为同一申请 → 去掉种类代码去重。"""
    m = re.match(r"^([A-Z]{2}\d{4,})", pn or "")
    return m.group(1) if m else pn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="")
    ap.add_argument("--country", default="US,EP,JP,WO,KR,DE")
    ap.add_argument("--pages", type=int, default=8)
    ap.add_argument("--detail", type=int, default=0)
    ap.add_argument("--detail-cap", type=int, default=400)
    ap.add_argument("--out", default="data/patent_google.jsonl")
    a = ap.parse_args()

    qs = [x.strip() for x in a.queries.split(",") if x.strip()] or QUERIES
    cts = [x.strip() for x in a.country.split(",") if x.strip()]
    combos = []
    for q in qs:
        for c in cts:
            combos.append((q, "%s country:%s" % (q, c)))

    print("组合数 %d（%d 词 × %d 国别）每组合上限 %d 页" % (
        len(combos), len(qs), len(cts), a.pages), flush=True)
    # 🔴 不用 &num=100：实测生效但排序崩坏（首条变无关专利）。每页固定 10 条。
    pager = detect_pager(combos[0][1])
    print("翻页写法: %r" % (pager or "无（只取首页）"), flush=True)

    seen, rows = set(), []
    n_skip = 0
    for i, (q, full) in enumerate(combos):
        got, total = 0, None
        for p in range(a.pages):
            extra = (pager % p) if (pager and p) else ""
            st, b = fetch(xhr_url(full, extra), headers={"Accept": "application/json"})
            if st != 200:
                break
            try:
                j = json.loads(b.decode("utf-8", "replace"))
                items = flatten(j)
                if total is None:
                    total = (j.get("results") or {}).get("total_num_results")
            except Exception as e:  # noqa: BLE001
                print("  !! 解析失败 %s: %s" % (full[:40], repr(e)[:80]), flush=True)
                break
            new = 0
            for it in items:
                key = base_key(it["pn"])
                if key in seen:
                    continue
                seen.add(key)
                txt = (it["title"] or "") + " " + (it["snippet"] or "")
                if not FRIC.search(txt):      # 排序崩坏时的噪声闸门
                    n_skip += 1
                    continue
                it["q"] = q
                rows.append(it)
                new += 1
            got += new
            if not items:
                break
            # 自适应：命中总数不足时别空翻
            if total and (p + 1) * 10 >= int(total):
                break
            time.sleep(0.7)
        print("  [%d/%d] %-56s total=%-7s 新增 %d（累计 %d，闸门剔 %d）" % (
            i + 1, len(combos), full[:56], total, got, len(rows), n_skip), flush=True)
        if len(rows) > 6000:
            print("  !! 已达上限，停止", flush=True)
            break

    n_det = 0
    if a.detail:
        targets = [r for r in rows if FORM.search((r["title"] or "") + " " + (r["snippet"] or ""))]
        targets = targets[:a.detail_cap]
        print("抓详情页：配方候选 %d 条，实际抓 %d 条" % (
            sum(1 for r in rows if FORM.search((r["title"] or "") + " " + (r["snippet"] or ""))),
            len(targets)), flush=True)
        for r in targets:
            d = detail(r["pn"])
            if d:
                r.update(d)
                n_det += 1
            time.sleep(0.5)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("PATENT_FETCH_DONE 条数=%d 详情页=%d → %s" % (len(rows), n_det, a.out), flush=True)


if __name__ == "__main__":
    main()
