# -*- coding: utf-8 -*-
"""GitHub Actions 侧：CORE（core.ac.uk）抓取 —— 摩擦/制动相关论文。

CORE = 全球最大 OA 仓储聚合器（1.3 亿篇），v3 API 需 Bearer key（免费注册）。
key 从环境变量 CORE_API_KEY 读（workflow 里由 repo secret 注入，绝不进仓库文件）。

核心价值（相对 OpenAlex）：
  1. documentType 含 doctoral/master/thesis —— 补海外博硕的**学位层级**；
  2. downloadUrl / fullText —— OA 全文层。

🔴 实测口径（2026-10-16）：
  - URL 必须带 trailing slash（否则 301）；
  - documentType 服务端检索**整体失效**（任何值返 HTTP 0 错误）→ 客户端过滤；
  - yearPublished 无冒号形式 `yearPublished>2019` 才有效（带冒号静默失败）；
  - 每条含 fullText ~300KB → limit 20-50、timeout 120、解析后丢弃 fullText；
  - documentType 可能是 list、可能空串（样本 ~40% 空）。

用法（单次抓取，双文件输出）：
  python scripts/core_fetch.py                 # 全部 + 博硕子集
  python scripts/core_fetch.py --min-year 2020 # 年份下限

输出：out/_core_works.jsonl（全部）+ out/_core_thesis.jsonl（博硕子集）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://api.core.ac.uk/v3/search/outputs/"   # 🔴 必须带 trailing slash
KEY = os.environ.get("CORE_API_KEY") or ""
PER_PAGE = 50           # v3 单请求上限 100；但每条含 fullText（~300KB），取 50 控响应体
MAX_PER_Q = 800         # 每词上限（简单搜索总量上限 10000）
OUT = os.path.join("out", "_core_works.jsonl")

QUERIES = [
    "brake pad", "brake lining", "friction material", "brake friction",
    "brake wear particle", "brake emission", "tribology brake",
    "regenerative braking friction", "brake noise squeal", "brake disc",
    "non-asbestos organic brake", "copper-free brake", "ceramic brake",
    "third body tribology", "brake dynamometer",
]


def get(url, tries=5):
    if not KEY:
        sys.stderr.write("!! 缺少 CORE_API_KEY 环境变量（repo secret 注入）\n")
        sys.exit(2)
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + KEY,
                "User-Agent": "friction-paper-core/1.0 (mailto:qilin.papers@outlook.com)"})
            with urllib.request.urlopen(req, timeout=120) as r:   # fullText 大响应体
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            body = ""
            try:
                body = e.read()[:200].decode("utf-8", "ignore")
            except Exception:
                pass
            if e.code in (429, 500, 502, 503):
                w = [10, 20, 40, 60, 90][a]
                print("    %s %s 退避 %ds" % (e.code, body[:80], w), flush=True)
                time.sleep(w)
                continue
            if e.code in (401, 403):
                print("!! key 无效或无权限：%s" % body[:120], flush=True)
                sys.exit(3)
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (a + 1))
    raise last  # type: ignore[misc]


def build_q(phrase, min_year):
    """CORE v3 是 Lucene 风格：题名或摘要命中 + 年份。
    🔴 documentType 服务端检索整体失效 → thesis 过滤在客户端 to_rec 做。"""
    parts = ['(title:"%s" OR abstract:"%s")' % (phrase, phrase)]
    if min_year:
        parts.append("yearPublished>%d" % (min_year - 1))   # 🔴 无冒号形式才有效
    return " AND ".join(parts)


def _to_int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _dt_str(v):
    """documentType 实测可能是 list（如 []）或 str。"""
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v or "")


def _first_dict(v):
    """list 字段元素实测可能是 dict 也可能是 str —— 取首个 dict 或空。"""
    if isinstance(v, list) and v:
        return v[0] if isinstance(v[0], dict) else {}
    return {}


def _names(v, limit=6):
    """subjects 实测可能是 [{'name':...}] 或 ['...'] 两种形态。"""
    out = []
    for t in (v or []):
        n = t.get("name") if isinstance(t, dict) else str(t)
        if n:
            out.append(n)
    return out[:limit]


def to_rec(r, phrase):
    repo = _first_dict(r.get("repositories"))
    ids = r.get("identifiers") or {}
    dt = _dt_str(r.get("documentType")).lower()
    # 🔴 客户端 thesis 过滤：显式关键词（dt 空值率 ~40%，非空也可能是 research article/text）
    is_thesis = any(k in dt for k in ("doctoral", "master", "phd", "thesis"))
    degree = ""
    if is_thesis:
        if "doctoral" in dt or "phd" in dt:
            degree = "博士"
        elif "master" in dt:
            degree = "硕士"
        else:
            degree = "学位论文"
    return {
        # 🔴 src 必须含 "diss" 子串，_make_site_data.py 才认 is_thesis
        "src": "core_diss" if is_thesis else "core",
        "coreid": str(r.get("id") or ""),
        "doi": str(r.get("doi") or ids.get("doi") or "").strip().lower(),
        "title": (r.get("title") or "").strip(),
        "year": _to_int(r.get("yearPublished")) or None,
        "cites": _to_int(r.get("citationCount")),
        "journal": "",
        "publisher": (r.get("publisher") or "").strip(),
        "first_author": (_first_dict(r.get("authors")).get("name") or "").strip(),
        "n_authors": len(r.get("authors") or []),
        "is_oa": True,
        "oa_url": r.get("downloadUrl") or "",
        "abstract": (r.get("abstract") or "").strip()[:1500],
        "matched": [phrase],
        "src_meta": {
            "school": (repo.get("name") or "").strip(),
            "degree": degree,
            "documentType": _dt_str(r.get("documentType")),
            "language": (r.get("language") or {}).get("name") or "",
            "topics": _names(r.get("subjects")),
        },
        "from": "core",
    }


def fetch_query(phrase, min_year, cap):
    q = build_q(phrase, min_year)
    offset = 0
    while offset < cap:
        url = API + "?" + urllib.parse.urlencode({
            "q": q, "offset": offset, "limit": PER_PAGE, "sort": "relevance"})
        j = get(url)
        results = j.get("results") or []
        for r in results:
            yield r
        total = (j.get("totalHits") or 0)
        offset += PER_PAGE
        if not results or offset >= min(total, 10000, cap):
            break
        time.sleep(1.0)   # 免费 tier 保守限速


def main():
    min_year = 2000
    for a in sys.argv[1:]:
        if a.startswith("--min-year"):
            try:
                min_year = int(a.split("=", 1)[1] if "=" in a else
                               sys.argv[sys.argv.index(a) + 1])
            except Exception:
                pass

    os.makedirs("out", exist_ok=True)
    seen = {}
    per_q = {}
    for q in QUERIES:
        cnt = 0
        try:
            for r in fetch_query(q, min_year, MAX_PER_Q):
                key = (r.get("doi") or "").lower() or "core:%s" % (r.get("id") or r.get("title"))
                if key not in seen:
                    seen[key] = to_rec(r, q)
                cnt += 1
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print("  !! [%s] 失败 %s" % (q, repr(e)[:120]), flush=True)
        per_q[q] = cnt
        print("  [%s] 命中 %d（累计 %d）" % (q, cnt, len(seen)), flush=True)
        time.sleep(1.0)

    out = list(seen.values())
    out_thesis = [r for r in out if r["src"] == "core_diss"]
    p_all = os.path.join("out", "_core_works.jsonl")
    p_thesis = os.path.join("out", "_core_thesis.jsonl")
    with open(p_all, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(p_thesis, "w", encoding="utf-8") as fh:
        for r in out_thesis:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("CORE_DONE 总数=%d 博硕=%d 输出=%s + %s 各词命中=%s"
          % (len(out), len(out_thesis), p_all, p_thesis, per_q), flush=True)


if __name__ == "__main__":
    main()
