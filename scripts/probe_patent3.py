# -*- coding: utf-8 -*-
"""probe_patent3.py —— 解决冒烟暴露的两个问题。

Q1: &num=100 是否让结果相关性崩塌？（对比默认 10 条的前 10 条标题）
Q2: q 里的 `country:US` 操作符在 XHR 里是否生效？（看 pn 前缀分布）
Q3: 精确短语（引号）能否显著提升精度？
Q4: &page=1 翻页是否真的换结果？
Q5: 每页默认返回几条？
"""
import json
import re
import sys
import urllib.parse

sys.path.insert(0, ".")
from patent_fetch import fetch, xhr_url, flatten, clean  # noqa: E402

OUT = {}


def probe(tag, inner_extra="", url_extra=""):
    q = "non-asbestos friction material composition"
    if inner_extra:
        q = q + " " + inner_extra
    inner = "q=" + urllib.parse.quote_plus(q)
    url = "https://patents.google.com/xhr/query?url=" + urllib.parse.quote(inner, safe="") + url_extra
    st, b = fetch(url, headers={"Accept": "application/json"})
    if st != 200:
        OUT[tag] = {"status": st, "err": b[:120].decode("utf-8", "replace")}
        return
    j = json.loads(b.decode("utf-8", "replace"))
    items = flatten(j)
    OUT[tag] = {
        "status": st,
        "total": ((j.get("results") or {}).get("total_num_results")),
        "n": len(items),
        "pn_pref": [i["pn"][:2] for i in items[:20]],
        "titles": [i["title"][:78] for i in items[:10]],
        "top": items[0]["pn"] if items else "",
    }


# A 基线（默认每页）
probe("A_baseline")
# B num=100
probe("B_num100", url_extra="&num=100")
# C country:US（放 q 里）
probe("C_countryUS", inner_extra="country:US")
# D country:US + num=100
probe("D_countryUS_num100", inner_extra="country:US", url_extra="&num=100")
# E 精确短语 + country:US
probe("E_phrase_US", inner_extra='country:US "non-asbestos friction material"')
# F 翻页 page=1
probe("F_page1", url_extra="&page=1")
# G 翻页 page=2
probe("G_page2", url_extra="&page=2")
# H 纯引号短语（不带 country）
probe("H_phrase_only", inner_extra='"non-asbestos friction material composition"')

with open("out/patent_probe3.json", "w", encoding="utf-8") as fh:
    json.dump(OUT, fh, ensure_ascii=False, indent=1)

for k, v in OUT.items():
    print("=== %s status=%s total=%s n=%s top=%s" % (
        k, v.get("status"), v.get("total"), v.get("n"), v.get("top")), flush=True)
    print("    pn前缀:", "".join(x + " " for x in (v.get("pn_pref") or [])), flush=True)
    for t in (v.get("titles") or [])[:6]:
        print("      ·", t, flush=True)
print("PROBE3_DONE", flush=True)
