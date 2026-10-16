# -*- coding: utf-8 -*-
"""GitHub Actions 侧：OpenAlex 增量抓取（周更，替代原 VPS 出口）。

为什么走 GH Actions：VPS（腾讯云）IP 被 OpenAlex IP 级长期 429 限流；
GH runner（Azure 出口）不受影响（oa_diss 已实测 42s 跑完）。

用法（workflow_dispatch 传 since，或本地测试）：
  python scripts/oa_incr_fetch.py --since 2026-10-16

输出：out/_oa_incr_works.jsonl（workflow 提交回仓库，本地 _incr_update.py 拉取）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAILTO = "qilin.papers@outlook.com"
UA = "friction-paper-incr/1.0 (mailto:%s)" % MAILTO
BASE = "https://api.openalex.org"
PER_PAGE = 200
MAX_WORKS = 4000          # 单次增量上限（保护礼貌池 + 执行时限）
OUT = os.path.join("out", "_oa_incr_works.jsonl")

QUERIES = [
    "brake pad", "brake lining", "friction material", "brake friction",
    "brake wear particle", "brake emission", "tribology brake",
    "regenerative braking friction", "brake noise squeal", "brake disc",
    "non-asbestos organic brake", "copper-free brake", "ceramic brake",
    "third body tribology", "brake dynamometer",
]


def norm_doi(d):
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d


def get(url, tries=6):
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503):
                w = [10, 20, 40, 60, 90, 120][a]
                print("    %s 退避 %ds" % (e.code, w), flush=True)
                time.sleep(w)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (a + 1))
    raise last  # type: ignore[misc]


def unabs(inv):
    if not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))


def to_rec(w):
    src = ((w.get("primary_location") or {}).get("source") or {})
    oa = w.get("open_access") or {}
    au = w.get("authorships") or []
    fa = ((au[0].get("author") or {}).get("display_name") if au else "") or ""
    return {
        "oaid": w.get("id"),
        "doi": norm_doi(w.get("doi") or ""),
        "title": (w.get("title") or "").strip(),
        "type": w.get("type") or "",
        "year": w.get("publication_year"),
        "cites": w.get("cited_by_count") or 0,
        "fwci": w.get("fwci"),
        "journal": (src.get("display_name") or "").strip(),
        "issn_l": src.get("issn_l") or "",
        "publisher": src.get("host_organization_name") or "",
        "first_author": fa,
        "n_authors": len(au),
        "is_oa": bool(oa.get("is_oa")),
        "oa_url": oa.get("oa_url") or "",
        "abstract": unabs(w.get("abstract_inverted_index"))[:1500],
        "oa_topics": [t.get("display_name") for t in ((w.get("topics") or [])[:6])
                      if t.get("display_name")],
        "from": "openalex-incr",
    }


def fetch_query(since, phrase, cap):
    """跑一个检索词，yield 命中 work（已超 cap 则停止）。"""
    params = {
        "filter": "from_publication_date:%s,has_abstract:true,title_and_abstract.search:%s"
                  % (since, phrase),
        "per_page": PER_PAGE,
        "select": "id,doi,title,publication_year,cited_by_count,fwci,type,"
                  "primary_location,open_access,authorships,abstract_inverted_index,topics",
        "mailto": MAILTO,
        "cursor": "*",
    }
    seen_local = 0
    while True:
        url = BASE + "/works?" + urllib.parse.urlencode(params)
        try:
            j = get(url)
        except Exception as e:  # noqa: BLE001
            print("  !! 查询失败 [%s]: %s" % (phrase, repr(e)[:100]), flush=True)
            break
        for w in (j.get("results") or []):
            yield w
            seen_local += 1
            if seen_local >= cap:
                return
        meta = j.get("meta") or {}
        nxt = (meta.get("next_cursor") or "").strip()
        if not nxt or nxt == params["cursor"]:
            break
        params["cursor"] = nxt


def main():
    forced = None
    for a in sys.argv[1:]:
        if a.startswith("--since"):
            forced = a.split("=", 1)[1] if "=" in a else \
                (sys.argv[sys.argv.index(a) + 1] if sys.argv.index(a) + 1 < len(sys.argv) else None)
    since = forced or (date.today() - timedelta(days=30)).isoformat()
    today = date.today().isoformat()
    print("增量窗口: %s → %s  (上限 %d 篇/词)" % (since, today, MAX_WORKS), flush=True)

    os.makedirs("out", exist_ok=True)
    seen = {}          # oaid -> rec（union 去重）
    per_q = {}
    for q in QUERIES:
        cnt = 0
        for w in fetch_query(since, q, MAX_WORKS):
            oa = w.get("id")
            if not oa:
                continue
            if oa not in seen:
                seen[oa] = to_rec(w)
            cnt += 1
        per_q[q] = cnt
        print("  [%s] 新增命中 %d（累计 %d）" % (q, cnt, len(seen)), flush=True)
        if len(seen) >= MAX_WORKS:
            print("  !! 已达上限 %d，停止后续查询" % MAX_WORKS, flush=True)
            break

    out = list(seen.values())
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("OA_INCR_DONE 总数=%d 各词命中=%s" % (len(out), per_q), flush=True)


if __name__ == "__main__":
    main()
